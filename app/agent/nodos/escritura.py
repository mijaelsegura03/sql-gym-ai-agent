"""Ruta de escritura con confirmación (spec técnico §5.2, §5.5, §7; RF-40 a RF-49).

``extraer_operacion`` (LLM) → ``validar_operacion`` (código, herramienta ``operacion_socio``) →
``confirmar`` (``interrupt()``: el grafo se pausa y la interfaz muestra la propuesta) →
``ejecutar_operacion`` (código, transacción atómica) → ``responder_operacion`` (plantillas es/en).

La operación solo se ejecuta si el grafo se reanuda con ``{"decision": "confirmar"}``, lo que hace
únicamente el botón **Confirmar** de la interfaz (RF-46). Escribir "sí" en el chat crea un hilo
nuevo y no reanuda nada (CA-57).
"""

from __future__ import annotations

from langgraph.types import interrupt
from langsmith import traceable

from app.agent.estado import EstadoAgente
from app.agent.nodos import cargar_prompt, modelos
from app.llm import con_reintentos
from app.ops import auditoria
from app.ops.ejecucion import ejecutar
from app.ops.mensajes import texto_cancelada, texto_propuesta, texto_rechazo, texto_resultado
from app.ops.modelos import ExtraccionOperacion, ResultadoValidacion
from app.ops.validacion import validar


def armar_prompt(estado: EstadoAgente) -> str:
    u = estado["usuario"]
    return (cargar_prompt("extraer_operacion")
            .replace("<<fecha>>", estado["fecha_hoy"].strftime("%Y-%m-%d"))
            .replace("<<nombre>>", u.nombre)
            .replace("<<sede>>", u.sede_nombre or "ninguna (administración central: todas las sedes)")
            .replace("<<mensaje>>", estado["mensaje"]))


def extraer_operacion(estado: EstadoAgente) -> dict:
    """Convierte el mensaje en una ``OperacionSocio`` con salida estructurada (§7.1)."""
    llm = modelos.llm_principal().with_structured_output(ExtraccionOperacion)
    extraccion: ExtraccionOperacion = con_reintentos(llm.invoke, armar_prompt(estado))
    return {"operacion": extraccion.operacion, "nodos": ["extraer_operacion"]}


@traceable(run_type="tool", name="operacion_socio")
def operacion_socio(operacion, usuario, hoy) -> ResultadoValidacion:
    """Herramienta ``operacion_socio``: resuelve el socio, verifica alcance y reglas y arma la propuesta (§7.2)."""
    return validar(operacion, usuario, hoy)


def validar_operacion(estado: EstadoAgente) -> dict:
    """Valida la operación. Si no es válida, la registra como rechazada (RF-49)."""
    usuario = estado["usuario"]
    r = operacion_socio(estado["operacion"], usuario, estado["fecha_hoy"])
    if not r.valida:
        auditoria.registrar("rechazada", usuario, r.operacion, avisos=r.errores,
                            detalle=estado["mensaje"][:300], trace_id=estado.get("trace_id"))
    return {"validacion": r, "propuesta": r.propuesta, "herramientas": ["operacion_socio"],
            "nodos": ["validar_operacion"]}


def ruta_despues_de_validar(estado: EstadoAgente) -> str:
    return "confirmar" if estado["validacion"].valida else "responder_operacion"


def confirmar(estado: EstadoAgente) -> dict:
    """Pausa el grafo con ``interrupt()`` hasta que el administrador presione Confirmar o Cancelar (RF-46, RF-47)."""
    valor = interrupt({"propuesta": estado["propuesta"].model_dump(mode="json")})
    decision = valor.get("decision") if isinstance(valor, dict) else None
    return {"decision": "confirmar" if decision == "confirmar" else "cancelar", "nodos": ["confirmar"]}


def ruta_despues_de_confirmar(estado: EstadoAgente) -> str:
    return "ejecutar_operacion" if estado.get("decision") == "confirmar" else "responder_operacion"


def ejecutar_operacion(estado: EstadoAgente) -> dict:
    """Revalida y ejecuta en una transacción atómica con auditoría (§7.3; RF-48, RNF-03)."""
    r = ejecutar(estado["propuesta"], estado["operacion"], estado["usuario"], estado["fecha_hoy"],
                 trace_id=estado.get("trace_id"))
    return {"ejecucion": r, "nodos": ["ejecutar_operacion"]}


def responder_operacion(estado: EstadoAgente) -> dict:
    """Arma el mensaje del rechazo, la cancelación o el resultado con plantillas (sin LLM)."""
    idioma = estado["clasificacion"].idioma
    propuesta = estado.get("propuesta")
    validacion = estado.get("validacion")
    if validacion is not None and not validacion.valida:
        texto = texto_rechazo(validacion.errores, validacion.operacion, idioma)
    elif estado.get("ejecucion") is not None:
        texto = texto_resultado(estado["ejecucion"], propuesta, idioma)
    else:  # cancelada (RF-47)
        auditoria.registrar("cancelada", estado["usuario"], propuesta.operacion, propuesta,
                            trace_id=estado.get("trace_id"))
        texto = texto_cancelada(propuesta, idioma)
    return {"respuesta": texto, "fuentes": [], "ruta_final": "escritura", "nodos": ["responder_operacion"]}


def texto_de_propuesta(estado: EstadoAgente) -> str:
    """Texto que acompaña a la tarjeta mientras la propuesta espera la decisión."""
    return texto_propuesta(estado["propuesta"], estado["clasificacion"].idioma)
