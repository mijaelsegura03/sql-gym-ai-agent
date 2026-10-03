"""Ejecución atómica, revalidación y auditoría (spec técnico §7.3; RF-48, RF-49, RNF-03)."""

from __future__ import annotations

import inspect
import re
from datetime import date

import pytest

from app.auth import Usuario
from app.ops import ejecucion
from app.ops.auditoria import registrar
from app.ops.ejecucion import ejecutar
from app.ops.modelos import AltaSocio, Baja, CambiosSocio, ContactoEmergencia, ModificacionSocio, ReferenciaSocio, Suspension
from app.ops.validacion import validar

pytestmark = [pytest.mark.db, pytest.mark.usefixtures("base_restaurada")]

HOY = date(2026, 10, 16)
CENTRAL = Usuario(perfil="admin", id=15, nombre="Romina Aguirre", sede_alcance=None, seudonimo="a")
NORTE = Usuario(perfil="admin", id=19, nombre="Marcela Suárez", sede_alcance=2, seudonimo="b")


def _socio_activo_con_membresia(su) -> dict:
    return su.execute("""SELECT s.id, s.dni FROM socio s WHERE s.estado='activo' AND EXISTS
                         (SELECT 1 FROM membresia m WHERE m.socio_id=s.id AND m.estado='activa' AND m.fecha_fin >= DATE '2026-10-16')
                         ORDER BY s.id LIMIT 1""").fetchone()


def _ultima_auditoria(su) -> dict:
    return su.execute("SELECT * FROM agente.auditoria ORDER BY id DESC LIMIT 1").fetchone()


def test_suspension_aplica_estado_y_membresias_juntos(su):
    s = _socio_activo_con_membresia(su)
    op = Suspension(socio=ReferenciaSocio(dni=s["dni"]), motivo="trato irrespetuoso al personal")
    propuesta = validar(op, CENTRAL, HOY).propuesta
    r = ejecutar(propuesta, op, CENTRAL, HOY, trace_id="traza-1")
    assert r.ok
    assert su.execute("SELECT estado FROM socio WHERE id=%s", (s["id"],)).fetchone()["estado"] == "suspendido"
    estados = {m["estado"] for m in su.execute("SELECT estado FROM membresia WHERE id = ANY(%s)", (propuesta.membresias_a_cancelar,)).fetchall()}
    assert estados == {"cancelada"}
    a = _ultima_auditoria(su)
    assert (a["resultado"], a["operacion"], a["socio_id"], a["trace_id"]) == ("ejecutada", "suspension", s["id"], "traza-1")
    assert a["motivo"] == "trato irrespetuoso al personal"
    assert a["valores_antes"] == {"estado": "activo"} and a["valores_despues"]["estado"] == "suspendido"


def test_falla_a_mitad_no_deja_cambios(su):
    s = _socio_activo_con_membresia(su)
    op = Suspension(socio=ReferenciaSocio(dni=s["dni"]), motivo="x")
    propuesta = validar(op, CENTRAL, HOY).propuesta

    def fallar(_conn):
        raise RuntimeError("falla simulada")

    r = ejecutar(propuesta, op, CENTRAL, HOY, _falla_simulada=fallar)
    assert not r.ok
    assert su.execute("SELECT estado FROM socio WHERE id=%s", (s["id"],)).fetchone()["estado"] == "activo"
    estados = {m["estado"] for m in su.execute("SELECT estado FROM membresia WHERE id = ANY(%s)", (propuesta.membresias_a_cancelar,)).fetchall()}
    assert "cancelada" not in estados
    assert _ultima_auditoria(su)["resultado"] == "fallida"


def test_revalidacion_si_cambio_el_estado(su):
    s = _socio_activo_con_membresia(su)
    op = Suspension(socio=ReferenciaSocio(dni=s["dni"]), motivo="x")
    propuesta = validar(op, CENTRAL, HOY).propuesta
    su.execute("UPDATE socio SET estado='suspendido' WHERE id=%s", (s["id"],))  # cambio entre propuesta y confirmación
    su.commit()
    r = ejecutar(propuesta, op, CENTRAL, HOY)
    assert not r.ok and r.errores[0].codigo == "estado_origen_invalido"
    assert _ultima_auditoria(su)["resultado"] == "fallida"


def test_revalidacion_dni_cargado_mientras_tanto(su):
    op = AltaSocio(dni="40111222", nombre="Ana", apellido="Torres", fecha_nacimiento=date(1995, 3, 12),
                   contacto_emergencia=ContactoEmergencia(nombre="Luis Torres", parentesco="padre", telefono="341 5551234"))
    propuesta = validar(op, NORTE, HOY).propuesta
    assert ejecutar(propuesta, op, NORTE, HOY).ok
    r = ejecutar(propuesta, op, NORTE, HOY)  # mismo DNI otra vez
    assert not r.ok and r.errores[0].codigo == "dni_repetido"
    assert su.execute("SELECT count(*) n FROM socio WHERE dni='40111222'").fetchone()["n"] == 1


def test_alta_crea_socio_activo_con_fecha_de_hoy(su):
    op = AltaSocio(dni="40111222", nombre="Ana", apellido="Torres", fecha_nacimiento=date(1995, 3, 12),
                   email="ana.torres@example.com",
                   contacto_emergencia=ContactoEmergencia(nombre="Luis Torres", parentesco="padre", telefono="341 5551234"))
    r = ejecutar(validar(op, NORTE, HOY).propuesta, op, NORTE, HOY)
    assert r.ok
    fila = su.execute("SELECT * FROM socio WHERE id=%s", (r.socio_id,)).fetchone()
    assert (fila["estado"], fila["fecha_alta"], fila["sede_principal_id"]) == ("activo", HOY, 2)
    assert fila["contacto_emergencia"] == "Luis Torres (padre) - 341 5551234"


def test_modificacion_solo_cambia_ese_dato(su):
    antes = su.execute("SELECT * FROM socio WHERE sede_principal_id=2 ORDER BY id LIMIT 1").fetchone()
    op = ModificacionSocio(socio=ReferenciaSocio(dni=antes["dni"]), cambios=CambiosSocio(telefono="341 5559876"))
    assert ejecutar(validar(op, NORTE, HOY).propuesta, op, NORTE, HOY).ok
    despues = su.execute("SELECT * FROM socio WHERE id=%s", (antes["id"],)).fetchone()
    assert despues["telefono"] == "341 5559876"
    assert {k: v for k, v in despues.items() if k != "telefono"} == {k: v for k, v in antes.items() if k != "telefono"}


def test_baja_cancela_la_pendiente(su):
    s = su.execute("""SELECT s.id, s.dni FROM socio s WHERE s.estado='activo'
                      AND EXISTS (SELECT 1 FROM membresia m WHERE m.socio_id=s.id AND m.estado='pendiente')
                      AND NOT EXISTS (SELECT 1 FROM membresia m WHERE m.socio_id=s.id AND m.estado IN ('activa','congelada')
                                      AND m.fecha_fin >= DATE '2026-10-16') ORDER BY s.id LIMIT 1""").fetchone()
    op = Baja(socio=ReferenciaSocio(dni=s["dni"]), motivo="baja voluntaria")
    assert ejecutar(validar(op, CENTRAL, HOY).propuesta, op, CENTRAL, HOY).ok
    assert su.execute("SELECT estado FROM socio WHERE id=%s", (s["id"],)).fetchone()["estado"] == "baja"
    assert not su.execute("SELECT 1 FROM membresia WHERE socio_id=%s AND estado='pendiente'", (s["id"],)).fetchone()
    assert su.execute("SELECT count(*) n FROM membresia WHERE socio_id=%s", (s["id"],)).fetchone()["n"] > 0  # historial


def test_registro_de_canceladas_y_rechazadas(su):
    s = _socio_activo_con_membresia(su)
    op = Suspension(socio=ReferenciaSocio(dni=s["dni"]), motivo="x")
    propuesta = validar(op, CENTRAL, HOY).propuesta
    registrar("cancelada", CENTRAL, propuesta.operacion, propuesta, trace_id="t-2")
    a = _ultima_auditoria(su)
    assert (a["resultado"], a["socio_id"], a["trace_id"]) == ("cancelada", s["id"], "t-2")
    registrar("rechazada", CENTRAL, "fuera_de_catalogo", detalle="borrar accesos")
    assert _ultima_auditoria(su)["resultado"] == "rechazada"
    assert su.execute("SELECT estado FROM socio WHERE id=%s", (s["id"],)).fetchone()["estado"] == "activo"


def test_el_modulo_solo_tiene_sql_fija():
    fuente = inspect.getsource(ejecucion)
    assert "execute(" in fuente
    # todas las llamadas a execute usan constantes del módulo o sql.SQL compuesto con identificadores de una lista cerrada
    llamadas = re.findall(r"\.execute\(([^,\)]+)", fuente)
    assert set(llamadas) <= {"_INSERT_SOCIO", "sentencia", "_UPDATE_ESTADO", "_CANCELAR_MEMBRESIAS"}
