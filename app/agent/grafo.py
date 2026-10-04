"""Construcción del grafo LangGraph y funciones de entrada del agente (spec técnico §5.3, §5.5, §5.6).

```
START → clasificar ─┬─ directa | fuera_dominio | necesita_contexto ─▶ responder_directo ─▶ END
                    ├─ (socio ∧ escritura) | (socio ∧ pide_info_interna) ─▶ rechazar_permiso ─▶ END
                    ├─ datos ─────▶ generar_sql ⇄ ejecutar_sql ─────────────▶ sintetizar ─▶ END
                    ├─ documentos ─▶ buscar_documentos ─────────────────────▶ sintetizar ─▶ END
                    ├─ hibrida ───▶ [generar_sql ⇄ ejecutar_sql] ∥ [buscar_documentos] ─▶ sintetizar ─▶ END
                    └─ escritura (admin) ─▶ extraer_operacion ─▶ validar_operacion
                                              ├─ inválida ─▶ responder_operacion ─▶ END
                                              └─ válida ──▶ confirmar (interrupt)
                                                              ├─ cancelar ─▶ responder_operacion ─▶ END
                                                              └─ confirmar ─▶ ejecutar_operacion ─▶ responder_operacion ─▶ END
```

- Las aristas que dependen del perfil se evalúan en código con ``usuario.perfil`` (RNF-01).
- ``sintetizar`` es un nodo diferido (``defer=True``): en la ruta híbrida espera a que terminen la
  rama de documentos y la rama SQL (que puede reintentar) y corre una sola vez.
- Cada mensaje usa un ``thread_id`` nuevo (RF-21). El checkpointer en memoria solo sostiene el
  ``interrupt()`` de una propuesta pendiente dentro de su hilo (§5.5).

Funciones de entrada: :func:`responder`, :func:`reanudar` y :func:`descartar`.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import date

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.agent.estado import EstadoAgente, RespuestaAgente
from app.agent.nodos.clasificar import clasificar, rechazar_permiso, responder_directo, ruta_despues_de_clasificar
from app.agent.nodos.documentos import buscar_documentos
from app.agent.nodos.escritura import (
    confirmar, ejecutar_operacion, extraer_operacion, responder_operacion, ruta_despues_de_confirmar,
    ruta_despues_de_validar, texto_de_propuesta, validar_operacion,
)
from app.agent.nodos.sintetizar import sintetizar
from app.agent.nodos.sql import ejecutar_sql, generar_sql, ruta_despues_de_ejecutar
from app.auth import Usuario
from app.config import configurar_trazas, get_settings
from app.ops import auditoria

log = logging.getLogger(__name__)

NOMBRE_TRAZA = "agente_gimnasio"
# Tipos propios que se guardan en el checkpoint mientras una propuesta espera la confirmación
_TIPOS_CHECKPOINT = [
    ("app.auth", "Usuario"), ("app.agent.estado", "Clasificacion"), ("app.rag.buscador", "Fragmento"),
    *[("app.ops.modelos", n) for n in ("AltaSocio", "ModificacionSocio", "CambiosSocio", "ReferenciaSocio",
                                       "ContactoEmergencia", "Suspension", "Reactivacion", "Baja", "FueraDeCatalogo",
                                       "Aviso", "SocioAfectado", "PropuestaCambio", "ResultadoValidacion",
                                       "ResultadoEjecucion")],
]
_CUOTA_AGOTADA = {
    "es": "Se alcanzó el límite diario gratuito del modelo de lenguaje, así que por ahora no puedo responder. "
          "Volvé a intentarlo más tarde (el límite se renueva todos los días).",
    "en": "The language model's free daily limit was reached, so I can't answer right now. "
          "Please try again later (the limit resets every day).",
}
_ERROR_INTERNO = {
    "es": "Tuve un problema interno y no pude responder. Por favor, volvé a intentarlo en unos segundos.",
    "en": "I ran into an internal problem and couldn't answer. Please try again in a few seconds.",
}


def construir_grafo(checkpointer=None):
    """Construye y compila el ``StateGraph`` del agente."""
    g = StateGraph(EstadoAgente)
    g.add_node("clasificar", clasificar)
    g.add_node("responder_directo", responder_directo)
    g.add_node("rechazar_permiso", rechazar_permiso)
    g.add_node("generar_sql", generar_sql)
    g.add_node("ejecutar_sql", ejecutar_sql)
    g.add_node("buscar_documentos", buscar_documentos)
    g.add_node("sintetizar", sintetizar, defer=True)
    g.add_node("extraer_operacion", extraer_operacion)
    g.add_node("validar_operacion", validar_operacion)
    g.add_node("confirmar", confirmar)
    g.add_node("ejecutar_operacion", ejecutar_operacion)
    g.add_node("responder_operacion", responder_operacion)

    g.add_edge(START, "clasificar")
    g.add_conditional_edges("clasificar", ruta_despues_de_clasificar,
                            ["responder_directo", "rechazar_permiso", "extraer_operacion", "generar_sql", "buscar_documentos"])
    g.add_edge("responder_directo", END)
    g.add_edge("rechazar_permiso", END)
    g.add_edge("generar_sql", "ejecutar_sql")
    g.add_conditional_edges("ejecutar_sql", ruta_despues_de_ejecutar, ["generar_sql", "sintetizar"])
    g.add_edge("buscar_documentos", "sintetizar")
    g.add_edge("sintetizar", END)
    g.add_edge("extraer_operacion", "validar_operacion")
    g.add_conditional_edges("validar_operacion", ruta_despues_de_validar, ["confirmar", "responder_operacion"])
    g.add_conditional_edges("confirmar", ruta_despues_de_confirmar, ["ejecutar_operacion", "responder_operacion"])
    g.add_edge("ejecutar_operacion", "responder_operacion")
    g.add_edge("responder_operacion", END)
    if checkpointer is None:
        checkpointer = MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=_TIPOS_CHECKPOINT))
    return g.compile(checkpointer=checkpointer)


_grafo = None
_lock = threading.Lock()


def get_grafo():
    """Grafo compilado, compartido por todo el proceso (la interfaz lo cachea con ``st.cache_resource``)."""
    global _grafo
    with _lock:
        if _grafo is None:
            configurar_trazas()
            _grafo = construir_grafo()
        return _grafo


def _config(usuario: Usuario, thread_id: str, run_id: uuid.UUID) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "run_id": run_id,
        "run_name": NOMBRE_TRAZA,
        "metadata": {"perfil": usuario.perfil, "usuario": usuario.seudonimo, "thread_id": thread_id},
        "tags": [f"perfil:{usuario.perfil}"],
        "recursion_limit": 30,
    }


def _etiquetar_traza(run_id: uuid.UUID, usuario: Usuario, ruta: str) -> None:
    """Agrega la ruta como tag de la traza raíz en LangSmith, en segundo plano."""
    if not get_settings().trazas_activas:
        return

    def tarea() -> None:
        try:
            from langchain_core.tracers.langchain import wait_for_all_tracers
            from langsmith import Client

            wait_for_all_tracers()
            Client().update_run(run_id, tags=[f"perfil:{usuario.perfil}", f"ruta:{ruta}", ruta])
        except Exception:  # noqa: BLE001 - la traza es best effort
            log.debug("No se pudo etiquetar la traza %s", run_id, exc_info=True)

    threading.Thread(target=tarea, daemon=True).start()


def _armar_respuesta(grafo, usuario: Usuario, thread_id: str, run_id: uuid.UUID, salida: dict, t0: float) -> RespuestaAgente:
    estado = grafo.get_state({"configurable": {"thread_id": thread_id}})
    valores: dict = dict(estado.values)
    pendiente = bool(salida.get("__interrupt__")) or "confirmar" in (estado.next or ())
    c = valores.get("clasificacion")
    idioma = c.idioma if c else "es"
    ruta = valores.get("ruta_final") or (c.ruta if c else "error")
    texto = texto_de_propuesta(valores) if pendiente else (valores.get("respuesta") or _ERROR_INTERNO[idioma])
    herramientas = list(dict.fromkeys(valores.get("herramientas") or []))
    respuesta = RespuestaAgente(
        texto=texto,
        ruta=ruta,
        idioma=idioma,
        herramientas=herramientas,
        fuentes=valores.get("fuentes") or [],
        sql=valores.get("sql") if usuario.es_admin and "consulta_sql" in herramientas else None,  # RF-56
        columnas=valores.get("columnas"),
        filas=valores.get("filas"),
        total_filas=valores.get("total_filas"),
        fragmentos=valores.get("fragmentos") or [],
        propuesta=valores.get("propuesta"),
        propuesta_pendiente=pendiente,
        resultado_operacion=valores.get("ejecucion"),
        nodos=valores.get("nodos") or [],
        thread_id=thread_id,
        trace_id=str(run_id),
        duracion_seg=round(time.perf_counter() - t0, 2),
    )
    _etiquetar_traza(run_id, usuario, ruta)
    return respuesta


def _es_cuota(error: BaseException) -> bool:
    from app.llm import CuotaDiariaAgotada

    while error is not None:
        if isinstance(error, CuotaDiariaAgotada):
            return True
        error = error.__cause__ or error.__context__
    return False


def _registrar_error(error: BaseException, contexto: str) -> None:
    if _es_cuota(error):
        log.warning("%s: se agotó la cuota diaria gratuita de Gemini", contexto)
    else:
        log.exception(contexto)


def _respuesta_error(usuario: Usuario, thread_id: str, run_id: uuid.UUID, idioma: str, t0: float,
                     error: BaseException | None = None) -> RespuestaAgente:
    textos = _CUOTA_AGOTADA if error is not None and _es_cuota(error) else _ERROR_INTERNO
    return RespuestaAgente(texto=textos[idioma], ruta="error", idioma=idioma, thread_id=thread_id,
                           trace_id=str(run_id), duracion_seg=round(time.perf_counter() - t0, 2), error=True)


def responder(usuario: Usuario, mensaje: str, hoy: date | None = None) -> RespuestaAgente:
    """Procesa un mensaje en un hilo nuevo (RF-21) y devuelve la respuesta para la interfaz.

    Si la ruta es de escritura y la operación es válida, el grafo queda pausado en ``confirmar`` y
    la respuesta trae ``propuesta_pendiente=True`` con su ``thread_id`` para reanudarlo.
    Ante un error interno devuelve un mensaje comprensible, sin trazas técnicas (RF-23).
    """
    grafo = get_grafo()
    thread_id, run_id, t0 = str(uuid.uuid4()), uuid.uuid4(), time.perf_counter()
    inicial: EstadoAgente = {
        "usuario": usuario, "mensaje": mensaje.strip(), "fecha_hoy": hoy or date.today(), "trace_id": str(run_id),
        "intentos_sql": 0, "herramientas": [], "nodos": [], "fragmentos": [], "fuentes": [],
    }
    try:
        salida = grafo.invoke(inicial, _config(usuario, thread_id, run_id))
    except Exception as e:  # noqa: BLE001
        _registrar_error(e, "Error procesando el mensaje")
        idioma = "en" if mensaje.isascii() and " the " in f" {mensaje.lower()} " else "es"
        return _respuesta_error(usuario, thread_id, run_id, idioma, t0, e)
    return _armar_respuesta(grafo, usuario, thread_id, run_id, salida, t0)


def _hilo_pendiente(grafo, usuario: Usuario, thread_id: str):
    """Estado del hilo si tiene una propuesta pendiente del mismo usuario; si no, ``None``."""
    estado = grafo.get_state({"configurable": {"thread_id": thread_id}})
    if not estado or "confirmar" not in (estado.next or ()):
        return None
    dueño = estado.values.get("usuario")
    if not dueño or dueño.id != usuario.id or dueño.perfil != usuario.perfil:
        return None
    return estado


def reanudar(usuario: Usuario, thread_id: str, decision: str) -> RespuestaAgente:
    """Reanuda el hilo de una propuesta con la decisión del botón: ``confirmar`` o ``cancelar`` (RF-46, RF-47).

    Si el hilo ya no tiene una propuesta pendiente (por ejemplo, porque ya se confirmó y se recargó
    la página), no ejecuta nada.
    """
    grafo = get_grafo()
    run_id, t0 = uuid.uuid4(), time.perf_counter()
    estado = _hilo_pendiente(grafo, usuario, thread_id)
    if estado is None:
        return RespuestaAgente(texto="Esta propuesta ya no está pendiente: no se ejecutó nada.",
                               ruta="escritura", thread_id=thread_id, trace_id=None, error=True)
    decision = "confirmar" if decision == "confirmar" else "cancelar"
    try:
        salida = grafo.invoke(Command(resume={"decision": decision}, update={"trace_id": str(run_id)}),
                              _config(usuario, thread_id, run_id))
    except Exception as e:  # noqa: BLE001
        _registrar_error(e, "Error reanudando la propuesta")
        return _respuesta_error(usuario, thread_id, run_id, estado.values["clasificacion"].idioma, t0, e)
    return _armar_respuesta(grafo, usuario, thread_id, run_id, salida, t0)


def descartar(usuario: Usuario, thread_id: str) -> bool:
    """Descarta una propuesta pendiente sin ejecutarla y la registra como ``descartada`` (RF-47, CA-56).

    Returns:
        True si había una propuesta pendiente y se descartó.
    """
    grafo = get_grafo()
    estado = _hilo_pendiente(grafo, usuario, thread_id)
    if estado is None:
        return False
    propuesta = estado.values.get("propuesta")
    auditoria.registrar("descartada", usuario, propuesta.operacion, propuesta, trace_id=estado.values.get("trace_id"))
    try:
        grafo.checkpointer.delete_thread(thread_id)
    except Exception:  # noqa: BLE001
        pass
    return True
