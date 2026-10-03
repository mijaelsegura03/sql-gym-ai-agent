"""Reglas de negocio de las operaciones de escritura (spec técnico §7.2; OP-01 a OP-05, RF-41 a RF-45).

Todo es código Python con consultas parametrizadas: ninguna regla depende del LLM. La fecha de
"hoy" es la real (RF-06) y se puede inyectar en los tests.

La misma función :func:`validar` se usa dos veces: al armar la propuesta (con ``gym_lector_admin``)
y al confirmar, dentro de la transacción de ejecución (con ``gym_escritor`` y la fila del socio
bloqueada), para revalidar (RF-48).
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import psycopg

from app.db.conexion import conectar
from app.ops.modelos import (
    AltaSocio, Aviso, Baja, FueraDeCatalogo, ModificacionSocio, PropuestaCambio, ReferenciaSocio,
    Reactivacion, ResultadoValidacion, SocioAfectado, Suspension,
)

EDAD_MINIMA = 16
EDAD_MAYORIA = 18
_RE_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CAMPOS_SOCIO = ["dni", "nombre", "apellido", "email", "telefono", "fecha_nacimiento", "contacto_emergencia",
                "sede_principal_id", "fecha_alta", "estado"]


def solo_digitos(texto: str | None) -> str:
    return "".join(c for c in (texto or "") if c.isdigit())


def edad(nacimiento: date, hoy: date) -> int:
    """Edad cumplida en años a la fecha ``hoy``."""
    return hoy.year - nacimiento.year - ((hoy.month, hoy.day) < (nacimiento.month, nacimiento.day))


def _a_json(valor: Any) -> Any:
    return valor.isoformat() if isinstance(valor, date) else valor


# ---------------------------------------------------------------------------
# Consultas auxiliares
# ---------------------------------------------------------------------------
def _fila_socio(conn: psycopg.Connection, socio_id: int, bloquear: bool = False) -> dict | None:
    sql = f"SELECT id, {', '.join(CAMPOS_SOCIO)} FROM public.socio WHERE id = %s" + (" FOR UPDATE" if bloquear else "")
    return conn.execute(sql, (socio_id,)).fetchone()


def resolver_socio(conn: psycopg.Connection, ref: ReferenciaSocio) -> tuple[dict | None, list[Aviso]]:
    """Resuelve el socio a exactamente uno, por DNI o, si no hay DNI, por nombre (RF-44)."""
    dni = solo_digitos(ref.dni)
    if dni:
        fila = conn.execute(f"SELECT id, {', '.join(CAMPOS_SOCIO)} FROM public.socio WHERE dni = %s", (dni,)).fetchone()
        return (fila, []) if fila else (None, [Aviso(codigo="socio_no_encontrado_dni", params={"dni": dni})])
    nombre = (ref.nombre or "").strip()
    if not nombre:
        return None, [Aviso(codigo="socio_sin_identificar")]
    filas = conn.execute(
        f"""SELECT id, {', '.join(CAMPOS_SOCIO)} FROM public.socio
            WHERE ext.unaccent(lower(nombre || ' ' || apellido)) = ext.unaccent(lower(%(t)s))
               OR ext.unaccent(lower(nombre)) = ext.unaccent(lower(%(t)s))
               OR ext.unaccent(lower(apellido)) = ext.unaccent(lower(%(t)s))
            ORDER BY apellido, nombre LIMIT 20""",
        {"t": nombre},
    ).fetchall()
    if not filas:
        return None, [Aviso(codigo="socio_no_encontrado_nombre", params={"nombre": nombre})]
    if len(filas) > 1:
        alternativas = [{"nombre": f"{f['nombre']} {f['apellido']}", "dni": f["dni"], "estado": f["estado"]} for f in filas]
        return None, [Aviso(codigo="socio_ambiguo", params={"nombre": nombre, "alternativas": alternativas})]
    return filas[0], []


def _resolver_sede(conn: psycopg.Connection, texto: str) -> dict | None:
    """'Norte', 'la sede del norte', 'Sede Norte' → fila de sede."""
    t = re.sub(r"\b(la|sede|del|de|el)\b", " ", (texto or "").lower())
    t = " ".join(t.split())
    if not t:
        return None
    return conn.execute(
        """SELECT id, nombre, activa FROM public.sede
           WHERE ext.unaccent(lower(nombre)) LIKE '%%' || ext.unaccent(%s) || '%%' ORDER BY id LIMIT 1""",
        (t,),
    ).fetchone()


def _nombre_sede(conn: psycopg.Connection, sede_id: int | None) -> str | None:
    if sede_id is None:
        return None
    fila = conn.execute("SELECT nombre FROM public.sede WHERE id = %s", (sede_id,)).fetchone()
    return fila["nombre"] if fila else None


def _membresias_en_curso(conn: psycopg.Connection, socio_id: int, estados: tuple[str, ...], hoy: date) -> list[dict]:
    return conn.execute(
        """SELECT m.id, m.estado, m.fecha_inicio, m.fecha_fin, p.nombre AS plan
           FROM public.membresia m JOIN public.plan p ON p.id = m.plan_id
           WHERE m.socio_id = %s AND m.estado = ANY(%s) AND m.fecha_fin >= %s
           ORDER BY m.fecha_fin""",
        (socio_id, list(estados), hoy),
    ).fetchall()


def _validar_email(conn: psycopg.Connection, email: str, excluir_id: int | None, errores: list[Aviso]) -> str:
    email = email.strip().lower()
    if not _RE_EMAIL.match(email):
        errores.append(Aviso(codigo="email_invalido", params={"email": email}))
    elif conn.execute("SELECT 1 FROM public.socio WHERE lower(email) = %s AND id IS DISTINCT FROM %s",
                      (email, excluir_id)).fetchone():
        errores.append(Aviso(codigo="email_repetido", params={"email": email}))
    return email


def _validar_edad(nacimiento: date, hoy: date, errores: list[Aviso], advertencias: list[Aviso]) -> None:
    if nacimiento > hoy:
        errores.append(Aviso(codigo="fecha_nacimiento_futura", params={"fecha": nacimiento.isoformat()}))
        return
    anios = edad(nacimiento, hoy)
    if anios < EDAD_MINIMA:
        errores.append(Aviso(codigo="menor_de_16", params={"edad": anios}))
    elif anios < EDAD_MAYORIA:
        advertencias.append(Aviso(codigo="autorizacion_menor", params={"edad": anios}))


def _socio_afectado(fila: dict) -> SocioAfectado:
    return SocioAfectado(id=fila["id"], nombre=f"{fila['nombre']} {fila['apellido']}", dni=fila["dni"])


def _en_alcance(usuario, fila: dict) -> bool:
    return usuario.sede_alcance is None or fila["sede_principal_id"] == usuario.sede_alcance


# ---------------------------------------------------------------------------
# Validación por operación
# ---------------------------------------------------------------------------
def _validar_alta(conn, op: AltaSocio, usuario, hoy: date) -> ResultadoValidacion:
    errores: list[Aviso] = []
    advertencias: list[Aviso] = []
    contacto = op.contacto_emergencia
    faltantes = [campo for campo, valor in [
        ("dni", op.dni), ("nombre", op.nombre), ("apellido", op.apellido), ("fecha_nacimiento", op.fecha_nacimiento),
        ("contacto_nombre", contacto and contacto.nombre), ("contacto_parentesco", contacto and contacto.parentesco),
        ("contacto_telefono", contacto and contacto.telefono),
    ] if not valor]
    if contacto is None or not (contacto.nombre or contacto.parentesco or contacto.telefono):
        faltantes = [f for f in faltantes if not f.startswith("contacto_")] + ["contacto_emergencia"]

    # Sede principal: la del administrador; si no tiene, es obligatoria (OP-01)
    sede_id = usuario.sede_alcance
    if op.sede:
        sede = _resolver_sede(conn, op.sede)
        if not sede:
            errores.append(Aviso(codigo="sede_inexistente", params={"sede": op.sede}))
        elif not sede["activa"]:
            errores.append(Aviso(codigo="sede_inactiva", params={"sede": sede["nombre"]}))
        elif usuario.sede_alcance is not None and sede["id"] != usuario.sede_alcance:
            errores.append(Aviso(codigo="sede_fuera_de_alcance", params={"sede": sede["nombre"],
                                                                          "alcance": _nombre_sede(conn, usuario.sede_alcance)}))
        else:
            sede_id = sede["id"]
    elif sede_id is None:
        faltantes.append("sede")
    if faltantes:
        errores.append(Aviso(codigo="faltan_datos", params={"campos": faltantes}))

    dni = solo_digitos(op.dni)
    if op.dni:
        if not 7 <= len(dni) <= 8:
            errores.append(Aviso(codigo="dni_invalido", params={"dni": op.dni}))
        elif conn.execute("SELECT 1 FROM public.socio WHERE dni = %s", (dni,)).fetchone():
            errores.append(Aviso(codigo="dni_repetido", params={"dni": dni}))
    email = _validar_email(conn, op.email, None, errores) if op.email else None
    if op.fecha_nacimiento:
        _validar_edad(op.fecha_nacimiento, hoy, errores, advertencias)

    if errores:
        return ResultadoValidacion(operacion="alta_socio", errores=errores)

    advertencias.append(Aviso(codigo="alta_requiere_membresia_y_apto"))
    contacto_txt = f"{contacto.nombre.strip()} ({contacto.parentesco.strip()}) - {contacto.telefono.strip()}"
    datos = {
        "dni": dni, "nombre": op.nombre.strip(), "apellido": op.apellido.strip(), "email": email,
        "telefono": op.telefono.strip() if op.telefono else None, "fecha_nacimiento": op.fecha_nacimiento.isoformat(),
        "contacto_emergencia": contacto_txt, "sede_principal_id": sede_id, "fecha_alta": hoy.isoformat(),
        "estado": "activo",
    }
    despues = {**{k: v for k, v in datos.items() if k != "sede_principal_id"}, "sede_principal": _nombre_sede(conn, sede_id)}
    propuesta = PropuestaCambio(
        operacion="alta_socio",
        socio=SocioAfectado(nombre=f"{datos['nombre']} {datos['apellido']}", dni=dni),
        antes={}, despues=despues, advertencias=advertencias, datos=datos, sede_alcance_admin=usuario.sede_alcance,
    )
    return ResultadoValidacion(operacion="alta_socio", propuesta=propuesta)


def _validar_modificacion(conn, op: ModificacionSocio, fila: dict, usuario, hoy: date) -> ResultadoValidacion:
    errores: list[Aviso] = []
    advertencias: list[Aviso] = []
    pedidos = op.cambios.pedidos()
    if "dni" in pedidos:
        errores.append(Aviso(codigo="dni_no_modificable"))
        pedidos.pop("dni")
    if not pedidos and not errores:
        errores.append(Aviso(codigo="sin_cambios"))

    datos: dict[str, Any] = {}
    for campo, valor in pedidos.items():
        if campo == "email":
            datos["email"] = _validar_email(conn, valor, fila["id"], errores)
        elif campo == "fecha_nacimiento":
            _validar_edad(valor, hoy, errores, advertencias)
            datos["fecha_nacimiento"] = valor
        elif campo == "sede_principal":
            sede = _resolver_sede(conn, valor)
            if not sede:
                errores.append(Aviso(codigo="sede_inexistente", params={"sede": valor}))
            elif not sede["activa"]:
                errores.append(Aviso(codigo="sede_inactiva", params={"sede": sede["nombre"]}))
            elif sede["id"] != fila["sede_principal_id"]:
                datos["sede_principal_id"] = sede["id"]
                advertencias.append(Aviso(codigo="cambio_sede_una_vez"))
                if usuario.sede_alcance is not None and sede["id"] != usuario.sede_alcance:
                    advertencias.append(Aviso(codigo="cambio_sede_fuera_de_alcance", params={"sede": sede["nombre"]}))
        else:
            datos[campo] = str(valor).strip()

    # Solo quedan los datos que realmente cambian
    datos = {k: v for k, v in datos.items() if fila.get(k) != v and not (k == "email" and (fila.get(k) or "").lower() == v)}
    if not datos and not errores:
        errores.append(Aviso(codigo="sin_cambios"))
    if errores:
        return ResultadoValidacion(operacion="modificacion_socio", errores=errores)

    def mostrar(campo: str, valor: Any) -> tuple[str, Any]:
        if campo == "sede_principal_id":
            return "sede_principal", _nombre_sede(conn, valor)
        return campo, _a_json(valor)

    antes = dict(mostrar(k, fila.get(k)) for k in datos)
    despues = dict(mostrar(k, v) for k, v in datos.items())
    propuesta = PropuestaCambio(
        operacion="modificacion_socio", socio=_socio_afectado(fila), antes=antes, despues=despues,
        advertencias=advertencias, datos={k: _a_json(v) for k, v in datos.items()}, sede_alcance_admin=usuario.sede_alcance,
    )
    return ResultadoValidacion(operacion="modificacion_socio", propuesta=propuesta)


def _validar_cambio_estado(conn, op, fila: dict, usuario, hoy: date) -> ResultadoValidacion:
    tipo = op.tipo
    errores: list[Aviso] = []
    advertencias: list[Aviso] = []
    efectos: list[Aviso] = []
    cancelar: list[int] = []
    origen_valido = {"suspension": ("activo",), "reactivacion": ("suspendido", "baja"), "baja": ("activo", "suspendido")}[tipo]
    destino = {"suspension": "suspendido", "reactivacion": "activo", "baja": "baja"}[tipo]
    if fila["estado"] not in origen_valido:
        errores.append(Aviso(codigo="estado_origen_invalido",
                             params={"operacion": tipo, "estado": fila["estado"], "permitidos": list(origen_valido)}))
    motivo = getattr(op, "motivo", None)
    if tipo in ("suspension", "baja") and not (motivo or "").strip():
        errores.append(Aviso(codigo="falta_motivo", params={"operacion": tipo}))

    if tipo == "baja":
        bloqueantes = _membresias_en_curso(conn, fila["id"], ("activa", "congelada"), hoy)
        if bloqueantes:
            m = bloqueantes[-1]
            errores.append(Aviso(codigo="baja_con_membresia_vigente",
                                 params={"estado": m["estado"], "plan": m["plan"], "fecha_fin": m["fecha_fin"].isoformat()}))
        estados_cancelar: tuple[str, ...] = ("pendiente",)
    elif tipo == "suspension":
        estados_cancelar = ("activa", "congelada", "pendiente")
    else:
        estados_cancelar = ()

    if errores:
        return ResultadoValidacion(operacion=tipo, errores=errores)

    for m in _membresias_en_curso(conn, fila["id"], estados_cancelar, hoy) if estados_cancelar else []:
        cancelar.append(m["id"])
        efectos.append(Aviso(codigo="membresia_cancelada", params={
            "id": m["id"], "plan": m["plan"], "estado": m["estado"], "fecha_fin": m["fecha_fin"].isoformat()}))
    if tipo == "suspension":
        advertencias.append(Aviso(codigo="suspension_reintegro"))
    elif tipo == "reactivacion":
        advertencias.append(Aviso(codigo="reactivacion_requiere_membresia"))
    elif tipo == "baja":
        advertencias.append(Aviso(codigo="baja_conserva_historial"))
        if cancelar:
            advertencias.append(Aviso(codigo="baja_cancela_pendiente"))

    propuesta = PropuestaCambio(
        operacion=tipo, socio=_socio_afectado(fila), antes={"estado": fila["estado"]}, despues={"estado": destino},
        efectos=efectos, advertencias=advertencias, motivo=(motivo or "").strip() or None,
        datos={"estado": destino}, membresias_a_cancelar=cancelar, sede_alcance_admin=usuario.sede_alcance,
    )
    return ResultadoValidacion(operacion=tipo, propuesta=propuesta)


def validar(op, usuario, hoy: date | None = None, conn: psycopg.Connection | None = None,
            bloquear_socio: bool = False) -> ResultadoValidacion:
    """Valida una operación con las reglas del §7.2 y arma la propuesta de cambio.

    Args:
        op: operación extraída (:data:`app.ops.modelos.OperacionSocio`).
        usuario: administrador de la sesión (:class:`app.auth.Usuario`).
        hoy: fecha real; inyectable en los tests.
        conn: conexión a usar (la de la transacción de ejecución al revalidar). Si es ``None``
            se abre una con ``gym_lector_admin``.
        bloquear_socio: si es True, bloquea la fila del socio con ``SELECT … FOR UPDATE``.
    """
    hoy = hoy or date.today()
    if conn is None:
        with conectar("lector_admin") as c:
            return validar(op, usuario, hoy, c)

    if not getattr(usuario, "es_admin", False):  # RF-40: también se controla en el grafo
        return ResultadoValidacion(operacion=getattr(op, "tipo", "?"), errores=[Aviso(codigo="sin_permiso_escritura")])
    if isinstance(op, FueraDeCatalogo):
        codigo = "operacion_masiva" if op.es_masiva else "fuera_de_catalogo"
        return ResultadoValidacion(operacion="fuera_de_catalogo",
                                   errores=[Aviso(codigo=codigo, params={"descripcion": op.descripcion})])
    if isinstance(op, AltaSocio):
        return _validar_alta(conn, op, usuario, hoy)

    fila, errores = resolver_socio(conn, op.socio)
    if errores:
        return ResultadoValidacion(operacion=op.tipo, errores=errores)
    if bloquear_socio:
        fila = _fila_socio(conn, fila["id"], bloquear=True)
    if not _en_alcance(usuario, fila):  # RF-41
        return ResultadoValidacion(operacion=op.tipo, errores=[Aviso(codigo="socio_fuera_de_alcance", params={
            "socio": f"{fila['nombre']} {fila['apellido']}", "sede": _nombre_sede(conn, fila["sede_principal_id"]),
            "alcance": _nombre_sede(conn, usuario.sede_alcance)})])
    if isinstance(op, ModificacionSocio):
        return _validar_modificacion(conn, op, fila, usuario, hoy)
    if isinstance(op, (Suspension, Reactivacion, Baja)):
        return _validar_cambio_estado(conn, op, fila, usuario, hoy)
    return ResultadoValidacion(operacion="fuera_de_catalogo", errores=[Aviso(codigo="fuera_de_catalogo",
                                                                             params={"descripcion": str(op)})])
