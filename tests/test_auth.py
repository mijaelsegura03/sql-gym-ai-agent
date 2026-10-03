"""Identificación por DNI (RF-50, RF-51; CA-80, CA-81, CA-86)."""

from __future__ import annotations

import pytest

from app.auth import identificar

pytestmark = pytest.mark.db


def _dni(su, consulta: str) -> str:
    return su.execute(consulta).fetchone()["dni"]


@pytest.mark.parametrize("estado", ["activo", "suspendido", "baja"])
def test_socio_valido_en_cualquier_estado(su, estado):
    dni = _dni(su, f"SELECT dni FROM socio WHERE estado='{estado}' ORDER BY id LIMIT 1")
    u = identificar("socio", dni)
    assert u and u.perfil == "socio" and not u.es_admin


@pytest.mark.parametrize("rol", ["administracion", "gerente", "recepcion"])
def test_admin_valido_los_tres_roles(su, rol):
    dni = _dni(su, f"SELECT dni FROM empleado WHERE rol='{rol}' AND activo ORDER BY id LIMIT 1")
    u = identificar("admin", dni)
    assert u and u.perfil == "admin" and u.rol == rol


def test_instructor_no_es_admin(su):
    assert identificar("admin", _dni(su, "SELECT dni FROM empleado WHERE rol='instructor' LIMIT 1")) is None


def test_empleado_inactivo_no_es_admin(su):
    # CA-81: recepcionista inactivo de la Sede Centro
    dni = _dni(su, "SELECT dni FROM empleado WHERE NOT activo AND rol='recepcion' AND sede_id=1")
    assert identificar("admin", dni) is None


def test_dni_de_empleado_con_perfil_socio(su):
    # CA-80
    dni = _dni(su, "SELECT dni FROM empleado e WHERE NOT EXISTS (SELECT 1 FROM socio s WHERE s.dni=e.dni) LIMIT 1")
    assert identificar("socio", dni) is None


def test_dni_de_socio_con_perfil_admin(su):
    assert identificar("admin", _dni(su, "SELECT dni FROM socio LIMIT 1")) is None


def test_dni_inexistente_o_vacio():
    assert identificar("socio", "11111111") is None
    assert identificar("socio", "") is None
    assert identificar("socio", "' OR '1'='1") is None  # consultas parametrizadas


def test_alcance_de_sede(su):
    central = identificar("admin", _dni(su, "SELECT dni FROM empleado WHERE rol='administracion' AND sede_id IS NULL LIMIT 1"))
    assert central.sede_alcance is None
    # CA-86: recepcionista activo de la Sede Sur
    sur = identificar("admin", _dni(su, "SELECT dni FROM empleado WHERE rol='recepcion' AND activo AND sede_id=3"))
    assert sur.sede_alcance == 3 and sur.sede_nombre == "Sede Sur"


def test_usuario_sin_dni_en_claro(su):
    dni = _dni(su, "SELECT dni FROM socio ORDER BY id LIMIT 1")
    u = identificar("socio", dni)
    assert dni not in u.model_dump_json()
    assert len(u.seudonimo) == 12
    assert identificar("socio", dni).seudonimo == u.seudonimo  # estable
