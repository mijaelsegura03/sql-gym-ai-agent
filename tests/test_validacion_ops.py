"""Reglas de negocio de las operaciones (spec técnico §7.2). Cada regla tiene un caso que pasa y uno que falla.

La fecha de "hoy" se inyecta (16/10/2026, la fecha base de los datos) para que los resultados no
dependan del día en que se corren los tests.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.auth import Usuario
from app.ops.mensajes import texto_aviso, texto_rechazo
from app.ops.modelos import (
    AltaSocio, Baja, CambiosSocio, ContactoEmergencia, FueraDeCatalogo, ModificacionSocio, Reactivacion,
    ReferenciaSocio, Suspension,
)
from app.ops.validacion import validar

pytestmark = pytest.mark.db

HOY = date(2026, 10, 16)
CENTRAL = Usuario(perfil="admin", id=15, nombre="Romina Aguirre", sede_alcance=None, seudonimo="a")
NORTE = Usuario(perfil="admin", id=19, nombre="Marcela Suárez", sede_alcance=2, sede_nombre="Sede Norte", seudonimo="b")
SOCIO = Usuario(perfil="socio", id=1, nombre="x", sede_alcance=1, seudonimo="c")


def codigos(resultado) -> set[str]:
    return {e.codigo for e in resultado.errores}


def advertencias(resultado) -> set[str]:
    return {a.codigo for a in resultado.propuesta.advertencias}


def dni(su, condicion: str) -> str:
    fila = su.execute(f"SELECT s.dni FROM socio s WHERE {condicion} ORDER BY s.id LIMIT 1").fetchone()
    assert fila, condicion
    return fila["dni"]


VIGENTE = "EXISTS (SELECT 1 FROM membresia m WHERE m.socio_id=s.id AND m.estado='{e}' AND m.fecha_fin >= DATE '2026-10-16')"


def alta(**cambios) -> AltaSocio:
    datos = dict(dni="40111222", nombre="Ana", apellido="Torres", fecha_nacimiento=date(1995, 3, 12),
                 email="ana.torres@example.com",
                 contacto_emergencia=ContactoEmergencia(nombre="Luis Torres", parentesco="padre", telefono="341 5551234"))
    datos.update(cambios)
    return AltaSocio(**datos)


# ---------------------------------------------------------------------------
# Resolución del socio y alcance
# ---------------------------------------------------------------------------
def test_socio_por_dni(su):
    r = validar(Reactivacion(socio=ReferenciaSocio(dni=dni(su, "s.estado='suspendido'"))), CENTRAL, HOY)
    assert r.valida


def test_socio_inexistente():
    r = validar(Reactivacion(socio=ReferenciaSocio(dni="99999999")), CENTRAL, HOY)
    assert codigos(r) == {"socio_no_encontrado_dni"}


def test_socio_ambiguo_por_nombre(su):
    nombre = su.execute("SELECT nombre FROM socio GROUP BY nombre HAVING count(*) > 1 ORDER BY nombre LIMIT 1").fetchone()["nombre"]
    r = validar(Suspension(socio=ReferenciaSocio(nombre=nombre), motivo="x"), CENTRAL, HOY)
    assert codigos(r) == {"socio_ambiguo"}
    assert len(r.errores[0].params["alternativas"]) > 1
    assert "DNI" in texto_aviso(r.errores[0])


def test_socio_unico_por_nombre_y_apellido(su):
    fila = su.execute("SELECT nombre, apellido FROM socio WHERE estado='suspendido' ORDER BY id LIMIT 1").fetchone()
    r = validar(Reactivacion(socio=ReferenciaSocio(nombre=f"{fila['nombre']} {fila['apellido']}")), CENTRAL, HOY)
    assert r.valida


def test_alcance_de_sede(su):
    norte = dni(su, "s.sede_principal_id=2 AND s.estado='activo'")
    centro = dni(su, "s.sede_principal_id=1 AND s.estado='activo'")
    assert validar(ModificacionSocio(socio=ReferenciaSocio(dni=norte), cambios=CambiosSocio(telefono="341 5559876")), NORTE, HOY).valida
    r = validar(ModificacionSocio(socio=ReferenciaSocio(dni=centro), cambios=CambiosSocio(email="x@example.com")), NORTE, HOY)
    assert codigos(r) == {"socio_fuera_de_alcance"}  # CA-62


def test_socio_no_puede_escribir():
    assert codigos(validar(alta(), SOCIO, HOY)) == {"sin_permiso_escritura"}


# ---------------------------------------------------------------------------
# Fuera de catálogo y masivas
# ---------------------------------------------------------------------------
def test_fuera_de_catalogo():
    r = validar(FueraDeCatalogo(descripcion="borrar accesos", es_masiva=False), CENTRAL, HOY)
    assert codigos(r) == {"fuera_de_catalogo"}
    assert "alta de socio" in texto_rechazo(r.errores, r.operacion, "es")


def test_masiva():
    assert codigos(validar(FueraDeCatalogo(descripcion="baja de todos los morosos", es_masiva=True), CENTRAL, HOY)) == {"operacion_masiva"}


# ---------------------------------------------------------------------------
# Alta
# ---------------------------------------------------------------------------
def test_alta_valida_toma_la_sede_del_admin():
    r = validar(alta(), NORTE, HOY)  # CA-50
    assert r.valida
    assert r.propuesta.datos["sede_principal_id"] == 2 and r.propuesta.despues["sede_principal"] == "Sede Norte"
    assert r.propuesta.datos["fecha_alta"] == HOY.isoformat()
    assert "alta_requiere_membresia_y_apto" in advertencias(r)


def test_alta_obligatorios():
    r = validar(AltaSocio(dni="40222333", nombre="Pedro", apellido="Gómez"), NORTE, HOY)  # CA-63
    assert codigos(r) == {"faltan_datos"}
    assert set(r.errores[0].params["campos"]) == {"fecha_nacimiento", "contacto_emergencia"}


def test_alta_contacto_incompleto():
    r = validar(alta(contacto_emergencia=ContactoEmergencia(nombre="Luis", telefono="341")), NORTE, HOY)
    assert r.errores[0].params["campos"] == ["contacto_parentesco"]


def test_alta_sede_obligatoria_para_admin_central():
    assert codigos(validar(alta(), CENTRAL, HOY)) == {"faltan_datos"}
    r = validar(alta(sede="la sede del sur"), CENTRAL, HOY)
    assert r.valida and r.propuesta.datos["sede_principal_id"] == 3


def test_alta_sede_inexistente_y_fuera_de_alcance():
    assert codigos(validar(alta(sede="Oeste"), CENTRAL, HOY)) == {"sede_inexistente"}
    assert codigos(validar(alta(sede="Centro"), NORTE, HOY)) == {"sede_fuera_de_alcance"}


def test_alta_dni(su):
    assert codigos(validar(alta(dni="123"), NORTE, HOY)) == {"dni_invalido"}
    assert codigos(validar(alta(dni=dni(su, "TRUE")), NORTE, HOY)) == {"dni_repetido"}  # CA-64
    assert validar(alta(dni="40.111.222"), NORTE, HOY).valida


def test_alta_email(su):
    assert codigos(validar(alta(email="no-es-email"), NORTE, HOY)) == {"email_invalido"}
    repetido = su.execute("SELECT email FROM socio WHERE email IS NOT NULL LIMIT 1").fetchone()["email"]
    assert codigos(validar(alta(email=repetido.upper()), NORTE, HOY)) == {"email_repetido"}
    assert validar(alta(email=None), NORTE, HOY).valida  # el email es opcional


def test_alta_edad():
    assert codigos(validar(alta(fecha_nacimiento=date(2011, 10, 17)), NORTE, HOY)) == {"menor_de_16"}  # CA-65: 14 años
    assert codigos(validar(alta(fecha_nacimiento=date(2010, 10, 17)), NORTE, HOY)) == {"menor_de_16"}  # cumple 16 mañana
    r = validar(alta(fecha_nacimiento=date(2009, 5, 1)), NORTE, HOY)  # CA-66: 17 años
    assert r.valida and "autorizacion_menor" in advertencias(r)
    r = validar(alta(fecha_nacimiento=date(2008, 10, 16)), NORTE, HOY)  # cumple 18 hoy
    assert r.valida and "autorizacion_menor" not in advertencias(r)
    assert codigos(validar(alta(fecha_nacimiento=date(2030, 1, 1)), NORTE, HOY)) == {"fecha_nacimiento_futura"}


# ---------------------------------------------------------------------------
# Modificación
# ---------------------------------------------------------------------------
def test_modificacion_solo_lo_que_cambia(su):
    fila = su.execute("SELECT dni, telefono FROM socio WHERE sede_principal_id=2 ORDER BY id LIMIT 1").fetchone()
    r = validar(ModificacionSocio(socio=ReferenciaSocio(dni=fila["dni"]), cambios=CambiosSocio(telefono="341 5559876")), NORTE, HOY)
    assert r.valida and r.propuesta.antes == {"telefono": fila["telefono"]} and r.propuesta.despues == {"telefono": "341 5559876"}


def test_modificacion_dni_no_modificable(su):
    r = validar(ModificacionSocio(socio=ReferenciaSocio(dni=dni(su, "s.sede_principal_id=2")), cambios=CambiosSocio(dni="1234567")), NORTE, HOY)
    assert "dni_no_modificable" in codigos(r)


def test_modificacion_sin_cambios(su):
    fila = su.execute("SELECT dni, telefono FROM socio WHERE sede_principal_id=2 ORDER BY id LIMIT 1").fetchone()
    r = validar(ModificacionSocio(socio=ReferenciaSocio(dni=fila["dni"]), cambios=CambiosSocio(telefono=fila["telefono"])), NORTE, HOY)
    assert codigos(r) == {"sin_cambios"}


def test_modificacion_email(su):
    d = dni(su, "s.sede_principal_id=2")
    otro = su.execute("SELECT email FROM socio WHERE email IS NOT NULL AND dni <> %s LIMIT 1", (d,)).fetchone()["email"]
    assert codigos(validar(ModificacionSocio(socio=ReferenciaSocio(dni=d), cambios=CambiosSocio(email=otro)), NORTE, HOY)) == {"email_repetido"}
    assert validar(ModificacionSocio(socio=ReferenciaSocio(dni=d), cambios=CambiosSocio(email="nuevo@example.com")), NORTE, HOY).valida


def test_modificacion_edad(su):
    d = dni(su, "s.sede_principal_id=2")
    assert codigos(validar(ModificacionSocio(socio=ReferenciaSocio(dni=d), cambios=CambiosSocio(fecha_nacimiento=date(2015, 1, 1))), NORTE, HOY)) == {"menor_de_16"}


def test_modificacion_cambio_de_sede(su):
    d = dni(su, "s.sede_principal_id=2")
    r = validar(ModificacionSocio(socio=ReferenciaSocio(dni=d), cambios=CambiosSocio(sede_principal="Sur")), NORTE, HOY)
    assert r.valida and {"cambio_sede_una_vez", "cambio_sede_fuera_de_alcance"} <= advertencias(r)
    r = validar(ModificacionSocio(socio=ReferenciaSocio(dni=d), cambios=CambiosSocio(sede_principal="Sur")), CENTRAL, HOY)
    assert r.valida and advertencias(r) == {"cambio_sede_una_vez"}
    assert codigos(validar(ModificacionSocio(socio=ReferenciaSocio(dni=d), cambios=CambiosSocio(sede_principal="Oeste")), NORTE, HOY)) == {"sede_inexistente"}


# ---------------------------------------------------------------------------
# Suspensión, reactivación y baja
# ---------------------------------------------------------------------------
def test_suspension_estado_de_origen(su):
    r = validar(Suspension(socio=ReferenciaSocio(dni=dni(su, "s.estado='suspendido'")), motivo="x"), CENTRAL, HOY)
    assert codigos(r) == {"estado_origen_invalido"}  # CA-70


def test_suspension_motivo_obligatorio(su):
    r = validar(Suspension(socio=ReferenciaSocio(dni=dni(su, "s.estado='activo'"))), CENTRAL, HOY)
    assert codigos(r) == {"falta_motivo"}


def test_suspension_cancela_membresias_en_curso(su):
    d = dni(su, "s.estado='activo' AND " + VIGENTE.format(e="activa"))
    r = validar(Suspension(socio=ReferenciaSocio(dni=d), motivo="trato irrespetuoso al personal"), CENTRAL, HOY)  # CA-52
    assert r.valida and r.propuesta.membresias_a_cancelar
    assert r.propuesta.despues == {"estado": "suspendido"} and "suspension_reintegro" in advertencias(r)


def test_suspension_cancela_congelada_y_pendiente(su):
    d = dni(su, "s.estado='activo' AND " + VIGENTE.format(e="congelada"))
    estados = {e.params["estado"] for e in validar(Suspension(socio=ReferenciaSocio(dni=d), motivo="x"), CENTRAL, HOY).propuesta.efectos}
    assert "congelada" in estados


def test_reactivacion(su):
    r = validar(Reactivacion(socio=ReferenciaSocio(dni=dni(su, "s.estado='baja'"))), CENTRAL, HOY)
    assert r.valida and "reactivacion_requiere_membresia" in advertencias(r)
    assert codigos(validar(Reactivacion(socio=ReferenciaSocio(dni=dni(su, "s.estado='activo'"))), CENTRAL, HOY)) == {"estado_origen_invalido"}


def test_baja_con_membresia_activa_se_rechaza(su):
    d = dni(su, "s.estado='activo' AND " + VIGENTE.format(e="activa"))
    r = validar(Baja(socio=ReferenciaSocio(dni=d), motivo="voluntaria"), CENTRAL, HOY)  # CA-71
    assert codigos(r) == {"baja_con_membresia_vigente"} and r.errores[0].params["fecha_fin"] >= HOY.isoformat()


def test_baja_con_membresia_congelada_se_rechaza(su):
    d = dni(su, "s.estado='activo' AND " + VIGENTE.format(e="congelada"))
    r = validar(Baja(socio=ReferenciaSocio(dni=d), motivo="voluntaria"), CENTRAL, HOY)  # CA-72
    assert codigos(r) == {"baja_con_membresia_vigente"} and r.errores[0].params["estado"] == "congelada"


def test_baja_cancela_pendiente(su):
    d = dni(su, "s.estado='activo' AND " + VIGENTE.format(e="pendiente") +
            " AND NOT " + VIGENTE.format(e="activa") + " AND NOT " + VIGENTE.format(e="congelada"))
    r = validar(Baja(socio=ReferenciaSocio(dni=d), motivo="voluntaria"), CENTRAL, HOY)  # CA-58
    assert r.valida and len(r.propuesta.membresias_a_cancelar) == 1
    assert "baja_cancela_pendiente" in advertencias(r)


def test_baja_sin_membresia(su):
    d = dni(su, "s.estado='suspendido'")
    r = validar(Baja(socio=ReferenciaSocio(dni=d), motivo="voluntaria"), CENTRAL, HOY)  # CA-53
    assert r.valida and r.propuesta.despues == {"estado": "baja"} and not r.propuesta.membresias_a_cancelar


def test_baja_motivo_y_estado(su):
    assert codigos(validar(Baja(socio=ReferenciaSocio(dni=dni(su, "s.estado='suspendido'"))), CENTRAL, HOY)) == {"falta_motivo"}
    assert codigos(validar(Baja(socio=ReferenciaSocio(dni=dni(su, "s.estado='baja'")), motivo="x"), CENTRAL, HOY)) == {"estado_origen_invalido"}


def test_mensajes_en_ingles(su):
    r = validar(Baja(socio=ReferenciaSocio(dni=dni(su, "s.estado='activo' AND " + VIGENTE.format(e="activa"))), motivo="x"), CENTRAL, HOY)
    texto = texto_rechazo(r.errores, r.operacion, "en")
    assert "can't be terminated" in texto and ("/2026" in texto or "/2027" in texto)
