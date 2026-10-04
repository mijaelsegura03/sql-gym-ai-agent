"""Plantillas en castellano e inglés para las operaciones de escritura (nodo ``responder_operacion``).

Las validaciones devuelven :class:`app.ops.modelos.Aviso` (código + parámetros) y acá se arma el
texto en el idioma del mensaje, sin pasar por el LLM. Las fechas se muestran dd/mm/aaaa en
castellano y mm/dd/yyyy en inglés (RF-22).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.ops.modelos import Aviso, PropuestaCambio, ResultadoEjecucion

Idioma = str  # "es" | "en"

OPERACIONES = {
    "es": {"alta_socio": "Alta de socio", "modificacion_socio": "Modificación de datos", "suspension": "Suspensión",
           "reactivacion": "Reactivación", "baja": "Baja", "fuera_de_catalogo": "Operación fuera del catálogo"},
    "en": {"alta_socio": "New member", "modificacion_socio": "Member data update", "suspension": "Suspension",
           "reactivacion": "Reactivation", "baja": "Membership termination (baja)", "fuera_de_catalogo": "Operation not in catalog"},
}

CAMPOS = {
    "es": {"dni": "DNI", "nombre": "nombre", "apellido": "apellido", "fecha_nacimiento": "fecha de nacimiento",
           "contacto_emergencia": "contacto de emergencia (nombre, parentesco y teléfono)",
           "contacto_nombre": "nombre del contacto de emergencia", "contacto_parentesco": "parentesco del contacto de emergencia",
           "contacto_telefono": "teléfono del contacto de emergencia", "sede": "sede principal", "email": "email",
           "telefono": "teléfono", "sede_principal": "sede principal", "estado": "estado", "fecha_alta": "fecha de alta"},
    "en": {"dni": "DNI (ID number)", "nombre": "first name", "apellido": "last name", "fecha_nacimiento": "date of birth",
           "contacto_emergencia": "emergency contact (name, relationship and phone)",
           "contacto_nombre": "emergency contact name", "contacto_parentesco": "emergency contact relationship",
           "contacto_telefono": "emergency contact phone", "sede": "home branch", "email": "email", "telefono": "phone",
           "sede_principal": "home branch", "estado": "status", "fecha_alta": "registration date"},
}

ESTADOS = {"en": {"activo": "active", "suspendido": "suspended", "baja": "terminated (baja)", "activa": "active",
                  "congelada": "frozen", "pendiente": "pending", "cancelada": "cancelled", "vencida": "expired"}}

OPERACIONES_DISPONIBLES = {
    "es": "alta de socio, modificación de datos de un socio, suspensión, reactivación y baja (de a un socio por vez)",
    "en": "new member, member data update, suspension, reactivation and termination (one member at a time)",
}

_PLANTILLAS: dict[str, dict[str, str]] = {
    "es": {
        "socio_no_encontrado_dni": "No encontré ningún socio con DNI {dni}.",
        "socio_no_encontrado_nombre": "No encontré ningún socio llamado «{nombre}». Indicá su DNI.",
        "socio_sin_identificar": "No se indicó a qué socio se refiere la operación. Indicá su DNI.",
        "socio_ambiguo": "Hay {n} socios que coinciden con «{nombre}»: {lista}. Reenviá la instrucción indicando el DNI.",
        "socio_fuera_de_alcance": "{socio} es socio de {sede} y tu alcance de escritura es {alcance}: no podés modificarlo.",
        "fuera_de_catalogo": "No puedo hacer eso ({descripcion}). Las operaciones disponibles son: {disponibles}.",
        "operacion_masiva": "No hago operaciones masivas ({descripcion}): cada operación afecta a un solo socio. "
                            "Si querés, te muestro la lista de socios a los que afectaría y después operás de a uno.",
        "sin_permiso_escritura": "Con tu perfil no se pueden hacer cambios. Acercate a recepción o administración.",
        "faltan_datos": "Faltan datos obligatorios: {campos}.",
        "dni_invalido": "El DNI «{dni}» no es válido: tiene que tener 7 u 8 dígitos.",
        "dni_repetido": "Ya existe un socio con DNI {dni}.",
        "email_invalido": "El email «{email}» no tiene un formato válido.",
        "email_repetido": "El email {email} ya está registrado para otro socio.",
        "fecha_nacimiento_futura": "La fecha de nacimiento ({fecha}) es posterior a hoy.",
        "menor_de_16": "No cumple la edad mínima de 16 años (tiene {edad}) (DOC-01 §2).",
        "autorizacion_menor": "Tiene {edad} años: necesita la autorización escrita de madre, padre o tutor, firmada en recepción (DOC-01 §2).",
        "sede_inexistente": "No existe la sede «{sede}».",
        "sede_inactiva": "La {sede} no está activa.",
        "sede_fuera_de_alcance": "Solo podés dar de alta socios de {alcance}, no de {sede}.",
        "alta_requiere_membresia_y_apto": "Todavía no puede ingresar: necesita contratar una membresía y presentar un apto médico vigente.",
        "dni_no_modificable": "El DNI no se puede modificar.",
        "sin_cambios": "No se indicó ningún dato para cambiar (o los datos nuevos son iguales a los actuales).",
        "cambio_sede_una_vez": "El cambio de sede principal se permite una vez por período de membresía (DOC-01 §5).",
        "cambio_sede_fuera_de_alcance": "Después del cambio el socio queda en {sede}, fuera de tu alcance: ya no vas a poder modificarlo.",
        "estado_origen_invalido": "No se puede: el socio está en estado «{estado}» y la operación requiere {permitidos}.",
        "falta_motivo": "Falta el motivo de la {operacion}.",
        "baja_con_membresia_vigente": "No se puede dar la baja: tiene una membresía {estado} ({plan}) hasta el {fecha_fin}. "
                                      "La baja se podrá dar a partir del día siguiente a esa fecha.",
        "membresia_cancelada": "La membresía {estado} «{plan}» (hasta el {fecha_fin}) pasa a cancelada.",
        "suspension_reintegro": "La membresía se cancela; administración evalúa caso por caso si corresponde un reintegro parcial (DOC-02 §11).",
        "reactivacion_requiere_membresia": "No se reactivan membresías anteriores: para ingresar necesita contratar una membresía nueva. "
                                           "No se cobra matrícula de reingreso (DOC-02 §8).",
        "baja_conserva_historial": "No se borra ningún dato: el historial del socio se conserva.",
        "baja_cancela_pendiente": "La membresía pendiente se cancela junto con la baja.",
        "estado_cambio": "Los datos del socio cambiaron desde que se armó la propuesta. Volvé a enviar la instrucción.",
        "error_ejecucion": "No se pudo ejecutar la operación por un error interno; no se hizo ningún cambio.",
    },
    "en": {
        "socio_no_encontrado_dni": "I couldn't find any member with DNI {dni}.",
        "socio_no_encontrado_nombre": "I couldn't find any member named “{nombre}”. Please provide their DNI.",
        "socio_sin_identificar": "The member was not specified. Please provide their DNI.",
        "socio_ambiguo": "{n} members match “{nombre}”: {lista}. Please resend the instruction with the DNI.",
        "socio_fuera_de_alcance": "{socio} belongs to {sede} and your write scope is {alcance}: you can't modify this member.",
        "fuera_de_catalogo": "I can't do that ({descripcion}). Available operations: {disponibles}.",
        "operacion_masiva": "I don't run bulk operations ({descripcion}): each operation affects a single member. "
                            "I can list the members it would affect so you can handle them one by one.",
        "sin_permiso_escritura": "Your profile can't make changes. Please contact the front desk or administration.",
        "faltan_datos": "Required data is missing: {campos}.",
        "dni_invalido": "DNI “{dni}” is not valid: it must have 7 or 8 digits.",
        "dni_repetido": "A member with DNI {dni} already exists.",
        "email_invalido": "Email “{email}” is not valid.",
        "email_repetido": "Email {email} is already registered for another member.",
        "fecha_nacimiento_futura": "The date of birth ({fecha}) is in the future.",
        "menor_de_16": "Does not meet the minimum age of 16 (is {edad}) (DOC-01 §2).",
        "autorizacion_menor": "Is {edad} years old: needs written authorization from a parent or guardian, signed at the front desk (DOC-01 §2).",
        "sede_inexistente": "Branch “{sede}” does not exist.",
        "sede_inactiva": "{sede} is not active.",
        "sede_fuera_de_alcance": "You can only register members for {alcance}, not {sede}.",
        "alta_requiere_membresia_y_apto": "They can't enter yet: they need a membership and a valid medical certificate.",
        "dni_no_modificable": "The DNI can't be changed.",
        "sin_cambios": "No data to change was given (or the new values equal the current ones).",
        "cambio_sede_una_vez": "The home branch can be changed once per membership period (DOC-01 §5).",
        "cambio_sede_fuera_de_alcance": "After the change the member belongs to {sede}, outside your scope: you won't be able to modify them anymore.",
        "estado_origen_invalido": "Not possible: the member's status is “{estado}” and the operation requires {permitidos}.",
        "falta_motivo": "The reason for the {operacion} is missing.",
        "baja_con_membresia_vigente": "The member can't be terminated: they have a {estado} membership ({plan}) until {fecha_fin}. "
                                      "Termination will be possible from the day after that date.",
        "membresia_cancelada": "The {estado} membership “{plan}” (until {fecha_fin}) will be cancelled.",
        "suspension_reintegro": "The membership is cancelled; administration decides case by case on a partial refund (DOC-02 §11).",
        "reactivacion_requiere_membresia": "Previous memberships are not reactivated: they need a new membership to enter. "
                                           "No re-enrollment fee is charged (DOC-02 §8).",
        "baja_conserva_historial": "No data is deleted: the member's history is kept.",
        "baja_cancela_pendiente": "The pending membership is cancelled together with the termination.",
        "estado_cambio": "The member's data changed since the proposal was made. Please resend the instruction.",
        "error_ejecucion": "The operation failed due to an internal error; no changes were made.",
    },
}


def fecha(valor: Any, idioma: Idioma) -> str:
    """'2026-10-16' → '16/10/2026' (es) o '10/16/2026' (en)."""
    try:
        d = valor if isinstance(valor, date) else date.fromisoformat(str(valor)[:10])
    except ValueError:
        return str(valor)
    return d.strftime("%m/%d/%Y" if idioma == "en" else "%d/%m/%Y")


def _valor(campo: str, valor: Any, idioma: Idioma) -> str:
    if valor in (None, ""):
        return "—"
    if campo in ("fecha_nacimiento", "fecha_alta", "fecha_fin"):
        return fecha(valor, idioma)
    if campo in ("estado",) and idioma == "en":
        return ESTADOS["en"].get(valor, valor)
    return str(valor)


def texto_aviso(aviso: Aviso, idioma: Idioma = "es") -> str:
    """Texto de un error, advertencia o efecto en el idioma indicado."""
    idioma = "en" if idioma == "en" else "es"
    p = dict(aviso.params)
    if "fecha_fin" in p:
        p["fecha_fin"] = fecha(p["fecha_fin"], idioma)
    if "fecha" in p:
        p["fecha"] = fecha(p["fecha"], idioma)
    if idioma == "en":
        for k in ("estado",):
            if k in p:
                p[k] = ESTADOS["en"].get(p[k], p[k])
    if "campos" in p:
        p["campos"] = ", ".join(CAMPOS[idioma].get(c, c) for c in p["campos"])
    if "permitidos" in p:
        estados = [ESTADOS["en"].get(e, e) if idioma == "en" else e for e in p["permitidos"]]
        p["permitidos"] = (" or " if idioma == "en" else " o ").join(f"«{e}»" for e in estados)
    if "operacion" in p:
        p["operacion"] = OPERACIONES[idioma].get(p["operacion"], p["operacion"]).lower()
    if "alternativas" in p:
        p["n"] = len(p["alternativas"])
        p["lista"] = "; ".join(f"{a['nombre']} (DNI {a['dni']})" for a in p["alternativas"])
    p.setdefault("disponibles", OPERACIONES_DISPONIBLES[idioma])
    plantilla = _PLANTILLAS[idioma].get(aviso.codigo, aviso.codigo)
    try:
        return plantilla.format(**p)
    except (KeyError, IndexError):
        return plantilla


def nombre_campo(campo: str, idioma: Idioma) -> str:
    return CAMPOS["en" if idioma == "en" else "es"].get(campo, campo)


def texto_rechazo(errores: list[Aviso], operacion: str, idioma: Idioma) -> str:
    """Mensaje cuando la validación falla (RF-45): qué falta o qué está mal y que reenvíe la instrucción."""
    en = idioma == "en"
    titulo = OPERACIONES["en" if en else "es"].get(operacion, operacion)
    lineas = [f"**{titulo}: {'not possible' if en else 'no se puede realizar'}.**", ""]
    lineas += [f"- {texto_aviso(e, idioma)}" for e in errores]
    codigos = {e.codigo for e in errores}
    if codigos & {"faltan_datos", "falta_motivo", "socio_sin_identificar", "socio_ambiguo", "socio_no_encontrado_nombre",
                  "email_invalido", "dni_invalido"}:
        lineas += ["", "Please resend the full instruction with the corrected data." if en
                   else "Reenviá la instrucción completa con los datos corregidos."]
    return "\n".join(lineas)


def texto_propuesta(p: PropuestaCambio, idioma: Idioma) -> str:
    """Resumen breve de la propuesta, para acompañar la tarjeta de confirmación."""
    en = idioma == "en"
    titulo = OPERACIONES["en" if en else "es"].get(p.operacion, p.operacion)
    socio = f"{p.socio.nombre} (DNI {p.socio.dni})"
    if en:
        return f"**Change proposal — {titulo}** for {socio}. Review it and press **Confirm** to apply it or **Cancel** to discard it."
    return f"**Propuesta de cambio — {titulo}** para {socio}. Revisala y presioná **Confirmar** para aplicarla o **Cancelar** para descartarla."


def filas_comparacion(p: PropuestaCambio, idioma: Idioma) -> list[dict[str, str]]:
    """Filas campo / antes / después para la tabla de la tarjeta de la propuesta."""
    en = idioma == "en"
    campos = list(dict.fromkeys([*p.antes.keys(), *p.despues.keys()]))
    return [{("Field" if en else "Campo"): nombre_campo(c, idioma),
             ("Before" if en else "Antes"): _valor(c, p.antes.get(c), idioma),
             ("After" if en else "Después"): _valor(c, p.despues.get(c), idioma)} for c in campos]


def texto_resultado(r: ResultadoEjecucion, p: PropuestaCambio, idioma: Idioma) -> str:
    """Mensaje tras confirmar (RF-48)."""
    en = idioma == "en"
    titulo = OPERACIONES["en" if en else "es"].get(p.operacion, p.operacion)
    socio = f"{p.socio.nombre} (DNI {p.socio.dni})"
    if not r.ok:
        motivos = " ".join(texto_aviso(e, idioma) for e in r.errores)
        return (f"**{titulo} not applied** for {socio}. {motivos}" if en
                else f"**{titulo} no aplicada** para {socio}. {motivos}")
    lineas = [f"**{titulo} applied** for {socio}." if en else f"**{titulo} realizada** para {socio}."]
    lineas += [f"- {texto_aviso(e, idioma)}" for e in p.efectos]
    if p.operacion in ("alta_socio", "reactivacion"):
        codigo = "alta_requiere_membresia_y_apto" if p.operacion == "alta_socio" else "reactivacion_requiere_membresia"
        lineas.append(f"- {texto_aviso(Aviso(codigo=codigo), idioma)}")
    lineas.append("Recorded in the audit log." if en else "Quedó registrada en la auditoría.")
    return "\n".join(lineas)


def texto_cancelada(p: PropuestaCambio, idioma: Idioma) -> str:
    """Mensaje cuando el administrador cancela la propuesta (RF-47)."""
    titulo = OPERACIONES["en" if idioma == "en" else "es"].get(p.operacion, p.operacion)
    if idioma == "en":
        return f"Proposal cancelled: {titulo.lower()} for {p.socio.nombre} was **not** applied. No changes were made."
    return f"Propuesta cancelada: la {titulo.lower()} de {p.socio.nombre} **no** se aplicó. No se hizo ningún cambio."
