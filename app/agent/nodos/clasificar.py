"""Nodos ``clasificar``, ``responder_directo`` y ``rechazar_permiso`` (spec técnico §5.2; RF-14 a RF-17, RF-40, RF-53).

``clasificar`` hace **una sola** llamada al modelo rápido con salida estructurada. Si la ruta es
``directa``, ``fuera_dominio`` o ``necesita_contexto``, la misma llamada trae la respuesta, así esos
mensajes se resuelven sin herramientas (RF-15, RF-16).
"""

from __future__ import annotations

from app.agent.estado import Clasificacion, EstadoAgente
from app.agent.nodos import cargar_prompt, modelos
from app.llm import con_reintentos

_RESPALDO_DIRECTA = {
    "es": "¡Hola! Soy el asistente del gimnasio. ¿En qué te puedo ayudar?",
    "en": "Hi! I'm the gym assistant. How can I help you?",
}

_RECHAZO_ESCRITURA = {
    "es": "Con tu perfil de socio no puedo hacer cambios en el sistema, ni siquiera sobre tus propios datos. "
          "Para modificar tus datos o tu membresía acercate a recepción o administración.",
    "en": "With a member profile I can't make changes in the system, not even to your own data. "
          "To update your data or membership, please contact the front desk or administration.",
}

_RECHAZO_INTERNA = {
    "es": "No tengo permiso para darte esa información con tu perfil. Puedo ayudarte con tus propios datos "
          "(membresía, pagos, apto, reservas, accesos y rutina), la grilla y los cupos de las clases, los planes "
          "y las políticas del gimnasio.",
    "en": "I'm not allowed to share that information with your profile. I can help with your own data "
          "(membership, payments, medical certificate, bookings, access and routine), class schedules and "
          "availability, plans and the gym policies.",
}


def armar_prompt(estado: EstadoAgente) -> str:
    """Completa el prompt de clasificación con el perfil, el nombre, la fecha y el mensaje."""
    u = estado["usuario"]
    perfil = "Administrador" if u.es_admin else "Socio"
    return (cargar_prompt("clasificar")
            .replace("<<perfil>>", perfil)
            .replace("<<nombre>>", u.nombre)
            .replace("<<fecha>>", estado["fecha_hoy"].strftime("%d/%m/%Y (%A)"))
            .replace("<<mensaje>>", estado["mensaje"]))


def clasificar(estado: EstadoAgente) -> dict:
    """Clasifica el mensaje con el modelo rápido (§5.4)."""
    llm = modelos.llm_rapido().with_structured_output(Clasificacion)
    c: Clasificacion = con_reintentos(llm.invoke, armar_prompt(estado))
    return {"clasificacion": c, "nodos": ["clasificar"]}


def ruta_despues_de_clasificar(estado: EstadoAgente) -> str | list[str]:
    """Arista condicional después de ``clasificar``. Las decisiones por perfil se toman en código (RNF-01)."""
    c = estado["clasificacion"]
    es_admin = estado["usuario"].es_admin
    if c.ruta in ("directa", "fuera_dominio", "necesita_contexto"):
        return "responder_directo"
    if not es_admin and (c.ruta == "escritura" or c.pide_info_interna):
        return "rechazar_permiso"
    if c.ruta == "escritura":
        return "extraer_operacion"
    if c.ruta == "datos":
        return "generar_sql"
    if c.ruta == "documentos":
        return "buscar_documentos"
    return ["generar_sql", "buscar_documentos"]  # hibrida: fan-out en paralelo


def responder_directo(estado: EstadoAgente) -> dict:
    """Copia la respuesta de la clasificación. No invoca herramientas (RF-15, RF-16)."""
    c = estado["clasificacion"]
    texto = (c.respuesta_directa or "").strip() or _RESPALDO_DIRECTA[c.idioma]
    return {"respuesta": texto, "fuentes": [], "ruta_final": c.ruta, "nodos": ["responder_directo"]}


def rechazar_permiso(estado: EstadoAgente) -> dict:
    """Respuesta fija para un socio que pide escribir (RF-40) o información interna (RF-53).

    No confirma ni niega que exista la información pedida.
    """
    c = estado["clasificacion"]
    textos = _RECHAZO_ESCRITURA if c.ruta == "escritura" else _RECHAZO_INTERNA
    return {"respuesta": textos[c.idioma], "fuentes": [], "ruta_final": c.ruta, "nodos": ["rechazar_permiso"]}
