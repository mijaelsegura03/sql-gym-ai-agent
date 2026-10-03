"""Verifica que los datos cargados tengan los casos que necesitan los criterios de aceptación (D-01, §9.4)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.db

FECHA_BASE = "DATE '2026-10-16'"


def _n(conn, consulta: str, params: tuple = ()) -> int:
    return conn.execute(consulta, params).fetchone()["n"]


def test_al_menos_15_registros_en_5_tablas(su):
    from scripts.reset_db import contar_registros

    conteo = contar_registros()
    assert sum(1 for n in conteo.values() if n >= 15) >= 5  # R-03


def test_membresias_activa_congelada_y_pendiente_sin_activa(su):
    assert _n(su, f"SELECT count(*) n FROM membresia WHERE estado='activa' AND fecha_fin >= {FECHA_BASE}")
    assert _n(su, f"SELECT count(*) n FROM membresia WHERE estado='congelada' AND fecha_fin >= {FECHA_BASE}")
    # CA-58: socio activo con una pendiente y ninguna activa ni congelada en curso
    assert _n(su, f"""
        SELECT count(*) n FROM socio s
        WHERE s.estado='activo'
          AND EXISTS (SELECT 1 FROM membresia m WHERE m.socio_id=s.id AND m.estado='pendiente')
          AND NOT EXISTS (SELECT 1 FROM membresia m WHERE m.socio_id=s.id
                          AND m.estado IN ('activa','congelada') AND m.fecha_fin >= {FECHA_BASE})""")


def test_socios_suspendidos_y_de_baja(su):
    assert _n(su, "SELECT count(*) n FROM socio WHERE estado='suspendido'")
    assert _n(su, "SELECT count(*) n FROM socio WHERE estado='baja'")


@pytest.mark.parametrize("motivo", ["cuota vencida", "membresía congelada", "membresía cancelada", "sin apto médico"])
def test_accesos_rechazados_por_cada_motivo(su, motivo):
    # Motivos de DOC-01 §4
    assert _n(su, "SELECT count(*) n FROM acceso WHERE NOT permitido AND motivo_rechazo=%s", (motivo,))


def test_aptos_vencidos(su):
    assert _n(su, f"""SELECT count(*) n FROM (SELECT socio_id, max(fecha_vencimiento) v FROM apto_medico
                     GROUP BY socio_id) x WHERE v < {FECHA_BASE}""")


def test_nombre_de_pila_repetido(su):
    # CA-17 / CA-69: no hay nombre y apellido repetidos, así que se prueba con el nombre de pila
    assert _n(su, "SELECT count(*) n FROM (SELECT nombre FROM socio GROUP BY nombre HAVING count(*) > 1) x")


def test_socios_en_las_tres_sedes(su):
    assert _n(su, "SELECT count(DISTINCT sede_principal_id) n FROM socio") == 3


def test_empleados_para_identificacion(su):
    # CA-81 y CA-86: recepcionista inactivo en Centro y recepcionista activo en Sur; admin central
    assert _n(su, "SELECT count(*) n FROM empleado WHERE rol='recepcion' AND NOT activo AND sede_id=1")
    assert _n(su, "SELECT count(*) n FROM empleado WHERE rol='recepcion' AND activo AND sede_id=3")
    assert _n(su, "SELECT count(*) n FROM empleado WHERE rol='administracion' AND activo AND sede_id IS NULL")


def test_no_quedan_fechas_del_sistema_en_el_script():
    texto = (Path(__file__).resolve().parent.parent / "data" / "gimnasio_datos.sql").read_text(encoding="utf-8")
    codigo = "\n".join(linea.split("--")[0] for linea in texto.splitlines())
    assert not re.search(r"CURRENT_DATE|NOW\(\)|LOCALTIMESTAMP", codigo)
