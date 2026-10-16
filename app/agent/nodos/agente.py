"""Agente ReAct: nodos ``agente`` y ``ejecutar_herramientas`` (spec técnico §5.2, §5.3, §5.7;
RF-01 a RF-13, RF-18, RF-24, RF-25, RNF-01, RNF-05, RNF-06).

``agente`` llama al modelo principal con ``bind_tools`` y la conversación interna del mensaje
(``mensajes_agente``). Devuelve un ``AIMessage``: con ``tool_calls`` si quiere consultar algo, o sin ellas
cuando ya reunió lo necesario. Las herramientas dependen de la ruta (:func:`app.agent.herramientas.herramientas_para`):
en ``lectura``, las de lectura y exploración; en ``escritura`` (solo administrador), además ``operacion_socio``.

``ejecutar_herramientas`` ejecuta todas las llamadas de lectura y exploración de ese mensaje como un solo
paso, con el usuario del estado, y agrega un ``ToolMessage`` por llamada. El ciclo se corta en
:data:`MAX_PASOS_AGENTE` (DT-08). Al salir, la lectura va a ``sintetizar`` (DT-09) y la escritura, siempre, a
``extraer_operacion``: con las aclaraciones de ``operacion_socio`` si el agente la pidió, o sin ellas si no
(DT-10). Así la validación y la confirmación nunca dependen del LLM (RF-25).
"""

from __future__ import annotations

import uuid
from functools import lru_cache

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.agent import herramientas as h
from app.agent.estado import ConsultaEjecutada, EstadoAgente, PasoAgente
from app.agent.nodos import cargar_prompt, modelos
from app.config import RAIZ

MAX_PASOS_AGENTE = 4  # rondas de herramientas por mensaje (DT-08)

_REGLAS_SOCIO = """- El usuario es un SOCIO: las vistas `mi_*` y `mis_*` ya están filtradas por él, no filtres por su id ni su DNI.
- No hay datos de otros socios ni métricas del negocio. Si la pregunta los pide, consultá solo lo que exista en las
  vistas permitidas (el resultado puede quedar vacío)."""
_REGLAS_ADMIN = "- El usuario es un ADMINISTRADOR: puede leer todas las tablas del catálogo, de las tres sedes."


@lru_cache(maxsize=2)
def catalogo(perfil: str) -> str:
    """Catálogo de esquema y semántica para el perfil (``app/db/catalogos/<perfil>.md``)."""
    return (RAIZ / "app" / "db" / "catalogos" / f"{'admin' if perfil == 'admin' else 'socio'}.md").read_text(encoding="utf-8")


def _ruta(estado: EstadoAgente) -> str:
    """``escritura`` solo para un administrador con un pedido de escritura; si no, ``lectura``."""
    c = estado.get("clasificacion")
    return "escritura" if c is not None and c.ruta == "escritura" and estado["usuario"].es_admin else "lectura"


def armar_prompt(estado: EstadoAgente) -> str:
    """Instrucciones del agente: tarea según la ruta, perfil, fecha, reglas de SQL y el catálogo del perfil."""
    u = estado["usuario"]
    return (cargar_prompt("agente")
            .replace("<<tarea>>", cargar_prompt(f"agente_tarea_{_ruta(estado)}").strip())
            .replace("<<perfil>>", "Administrador" if u.es_admin else "Socio")
            .replace("<<nombre>>", u.nombre)
            .replace("<<sede>>", (u.sede_nombre or "ninguna (administración central: todas las sedes)") if u.es_admin else "")
            .replace("<<fecha>>", estado["fecha_hoy"].strftime("%Y-%m-%d (%A)"))
            .replace("<<reglas_perfil>>", _REGLAS_ADMIN if u.es_admin else _REGLAS_SOCIO)
            .replace("<<catalogo>>", catalogo(u.perfil)))


def agente(estado: EstadoAgente) -> dict:
    """Decide el siguiente paso con el modelo principal y las herramientas de la ruta."""
    previos = list(estado.get("mensajes_agente") or [])
    nuevos = [] if previos else [SystemMessage(armar_prompt(estado)), HumanMessage(estado["mensaje"])]
    respuesta = modelos.invocar("principal", previos + nuevos,
                                herramientas=h.herramientas_para(_ruta(estado), estado["usuario"]))
    return {"mensajes_agente": nuevos + [respuesta], "nodos": ["agente"]}


def llamada_operacion(estado: EstadoAgente) -> dict | None:
    """La ``tool_call`` de ``operacion_socio`` del último mensaje del agente, si la pidió en la ruta ``escritura``."""
    if _ruta(estado) != "escritura":
        return None
    ultimo = (estado.get("mensajes_agente") or [None])[-1]
    return next((c for c in getattr(ultimo, "tool_calls", None) or [] if c.get("name") == h.OPERACION), None)


def ruta_despues_de_agente(estado: EstadoAgente) -> str:
    """Arista después de ``agente`` (§5.3).

    - Escritura: si pide ``operacion_socio`` (aunque se haya llegado al límite), a ``extraer_operacion``.
    - Si pide otras herramientas y quedan pasos, a ``ejecutar_herramientas``.
    - Si no: la lectura va a ``sintetizar`` y la escritura a ``extraer_operacion`` sin aclaraciones (DT-10).
    """
    escritura = _ruta(estado) == "escritura"
    if escritura and llamada_operacion(estado):
        return "extraer_operacion"
    ultimo = (estado.get("mensajes_agente") or [None])[-1]
    if getattr(ultimo, "tool_calls", None) and estado.get("pasos_agente", 0) < MAX_PASOS_AGENTE:
        return "ejecutar_herramientas"
    return "extraer_operacion" if escritura else "sintetizar"


def _sin_repetir(previos, nuevos):
    vistos = {f.clave for f in previos}
    salida = list(previos)
    for f in nuevos:
        if f.clave not in vistos:
            vistos.add(f.clave)
            salida.append(f)
    return salida


def ejecutar_herramientas(estado: EstadoAgente) -> dict:
    """Ejecuta las ``tool_calls`` de lectura y exploración del último mensaje con el usuario del estado (RNF-01)."""
    usuario = estado["usuario"]
    llamadas = estado["mensajes_agente"][-1].tool_calls
    mensajes, consultas, pasos, herramientas = [], [], [], []
    fragmentos = list(estado.get("fragmentos") or [])
    for llamada in llamadas:
        nombre, args = llamada.get("name"), llamada.get("args") or {}
        id_llamada = llamada.get("id") or str(uuid.uuid4())
        if nombre == "consulta_sql":
            sql = str(args.get("sql") or "")
            r = h.consulta_sql(sql, usuario)
            consultas.append(ConsultaEjecutada(sql=r.sql, ok=r.ok, error=r.error,
                                               columnas=r.columnas if r.ok else None,
                                               filas=r.filas if r.ok else None,
                                               total_filas=r.total_filas if r.ok else None))
            pasos.append(PasoAgente(herramienta="consulta_sql", argumentos={"sql": sql}, resumen=h.resumen_sql(r)))
            contenido = h.texto_resultado_sql(r)
        elif nombre == "busqueda_documentos":
            consulta = str(args.get("consulta") or "").strip() or estado["mensaje"]
            encontrados = h.busqueda_documentos(consulta)
            fragmentos = _sin_repetir(fragmentos, encontrados)
            pasos.append(PasoAgente(herramienta="busqueda_documentos", argumentos={"consulta": consulta},
                                    resumen=h.resumen_busqueda(encontrados)))
            contenido = h.texto_resultado_busqueda(encontrados)
        elif nombre in ("describir_tabla", "valores_columna"):
            tabla, columna = str(args.get("tabla") or ""), str(args.get("columna") or "")
            if nombre == "describir_tabla":
                r, argumentos = h.describir_tabla(tabla, usuario), {"tabla": tabla}
            else:
                r, argumentos = h.valores_columna(tabla, columna, usuario), {"tabla": tabla, "columna": columna}
            pasos.append(PasoAgente(herramienta=nombre, argumentos=argumentos, resumen=h.resumen_exploracion(r)))
            contenido = h.texto_resultado_exploracion(r)
        else:  # RF-25: operacion_socio fuera de la escritura, o una herramienta inventada
            mensajes.append(ToolMessage(
                f"ERROR: la herramienta {nombre!r} no está disponible. Solo podés usar {', '.join(h.NOMBRES)}.",
                tool_call_id=id_llamada, name=str(nombre), status="error"))
            continue
        herramientas.append(nombre)
        mensajes.append(ToolMessage(contenido, tool_call_id=id_llamada, name=nombre))

    salida = {"mensajes_agente": mensajes, "consultas": consultas, "pasos": pasos, "herramientas": herramientas,
              "fragmentos": fragmentos, "pasos_agente": estado.get("pasos_agente", 0) + 1,
              "nodos": ["ejecutar_herramientas"]}
    todas = list(estado.get("consultas") or []) + consultas
    exitosas = [c for c in todas if c.ok]
    if exitosas:  # sql, filas, columnas y total: los de la última consulta exitosa (§5.1)
        c = exitosas[-1]
        salida.update(sql=c.sql, sql_error=None, filas=c.filas, columnas=c.columnas, total_filas=c.total_filas)
    elif todas:
        c = todas[-1]
        salida.update(sql=c.sql, sql_error=c.error, filas=None, columnas=None, total_filas=None)
    return salida
