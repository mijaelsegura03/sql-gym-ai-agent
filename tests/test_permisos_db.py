"""Permisos de los roles de la base (spec técnico §4.3, §4.4; RNF-01, RNF-02)."""

from __future__ import annotations

import psycopg
import pytest

from app.db.conexion import conectar

pytestmark = pytest.mark.db

VISTAS_PROPIAS = ["mi_socio", "mis_aptos", "mis_membresias", "mis_pagos", "mis_reservas",
                  "mis_accesos", "mis_rutinas", "mis_rutina_ejercicios"]
VISTAS_PUBLICAS = ["sede", "sala", "actividad", "plan", "plan_actividad", "ejercicio", "clase",
                   "sesion_clase", "ocupacion_sesion"]


def _socio_con_datos(su) -> int:
    """Un socio que tiene aptos, membresías, pagos, reservas, accesos y rutinas."""
    return su.execute("""
        SELECT s.id FROM socio s
        WHERE EXISTS (SELECT 1 FROM reserva r WHERE r.socio_id=s.id)
          AND EXISTS (SELECT 1 FROM rutina r WHERE r.socio_id=s.id)
          AND EXISTS (SELECT 1 FROM acceso a WHERE a.socio_id=s.id)
          AND EXISTS (SELECT 1 FROM apto_medico a WHERE a.socio_id=s.id)
        ORDER BY s.id LIMIT 1""").fetchone()["id"]


def test_lector_socio_no_lee_public():
    with conectar("lector_socio") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT * FROM public.socio LIMIT 1")


def test_lector_socio_sin_ningun_permiso_en_public(su):
    n = su.execute("""SELECT count(*) n FROM information_schema.role_table_grants
                      WHERE grantee='gym_lector_socio' AND table_schema='public'""").fetchone()["n"]
    assert n == 0
    assert not su.execute("SELECT has_schema_privilege('gym_lector_socio','public','USAGE') ok").fetchone()["ok"]


@pytest.mark.parametrize("vista", VISTAS_PROPIAS)
def test_vistas_propias_solo_del_socio_fijado(su, vista):
    socio = _socio_con_datos(su)
    with conectar("lector_socio") as conn:
        conn.execute("SELECT set_config('app.socio_id', %s, true)", (str(socio),))
        filas = conn.execute(f"SELECT * FROM {vista}").fetchall()
    assert filas, f"{vista} vuelve vacía para el socio {socio}"
    if vista == "mi_socio":
        assert {f["id"] for f in filas} == {socio}
    elif vista == "mis_pagos":
        ids = su.execute("SELECT p.id FROM pago p JOIN membresia m ON m.id=p.membresia_id WHERE m.socio_id=%s",
                         (socio,)).fetchall()
        assert {f["id"] for f in filas} == {i["id"] for i in ids}
    elif vista == "mis_rutina_ejercicios":
        rutinas = {r["id"] for r in su.execute("SELECT id FROM rutina WHERE socio_id=%s", (socio,)).fetchall()}
        assert {f["rutina_id"] for f in filas} <= rutinas
    else:
        assert {f["socio_id"] for f in filas} == {socio}


@pytest.mark.parametrize("vista", VISTAS_PROPIAS)
def test_vistas_propias_vacias_sin_socio_fijado(vista):
    with conectar("lector_socio") as conn:
        assert conn.execute(f"SELECT count(*) n FROM {vista}").fetchone()["n"] == 0


@pytest.mark.parametrize("vista", VISTAS_PUBLICAS)
def test_vistas_publicas_legibles(vista):
    with conectar("lector_socio") as conn:
        assert conn.execute(f"SELECT count(*) n FROM {vista}").fetchone()["n"] > 0


def test_columnas_sensibles_no_expuestas(su):
    def columnas(vista: str) -> set[str]:
        return {r["column_name"] for r in su.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='socio_api' AND table_name=%s",
            (vista,)).fetchall()}

    assert "registrado_por" not in columnas("mis_pagos")
    for vista in ("clase", "sesion_clase"):
        cols = columnas(vista)
        assert "instructor_nombre" in cols and "instructor_id" not in cols
    assert columnas("ocupacion_sesion") == {"sesion_id", "cupo", "confirmadas", "en_lista_espera", "lugares_disponibles"}


@pytest.mark.parametrize("sentencia", [
    "INSERT INTO sede (nombre, direccion) VALUES ('x','y')",
    "UPDATE socio SET telefono='1' WHERE id=1",
    "DELETE FROM acceso WHERE id=1",
])
def test_lector_admin_no_escribe(sentencia):
    with conectar("lector_admin") as conn:
        with pytest.raises((psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)):
            conn.execute(sentencia)


def test_lector_admin_no_escribe_aunque_pida_read_write():
    with conectar("lector_admin") as conn:
        conn.execute("SET TRANSACTION READ WRITE")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("UPDATE socio SET telefono='1' WHERE id=1")


def test_ningun_rol_tiene_delete(su):
    n = su.execute("""SELECT count(*) n FROM information_schema.role_table_grants
                      WHERE grantee IN ('gym_lector_admin','gym_lector_socio','gym_escritor')
                        AND privilege_type IN ('DELETE','TRUNCATE')""").fetchone()["n"]
    assert n == 0


def test_escritor_no_puede_borrar():
    with conectar("escritor") as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DELETE FROM socio WHERE id = -1")


def test_escritor_solo_donde_indica_el_spec(su):
    filas = su.execute("""SELECT table_schema||'.'||table_name t, privilege_type p
                          FROM information_schema.role_table_grants
                          WHERE grantee='gym_escritor' AND privilege_type IN ('INSERT','UPDATE')""").fetchall()
    assert {(f["t"], f["p"]) for f in filas} == {
        ("public.socio", "INSERT"), ("public.socio", "UPDATE"), ("agente.auditoria", "INSERT")}
    cols = su.execute("""SELECT column_name FROM information_schema.column_privileges
                         WHERE grantee='gym_escritor' AND table_name='membresia' AND privilege_type='UPDATE'""").fetchall()
    assert {c["column_name"] for c in cols} == {"estado"}
    for sentencia in ("UPDATE membresia SET fecha_fin = fecha_fin WHERE id = -1",
                      "UPDATE pago SET estado = 'aprobado' WHERE id = -1"):
        with conectar("escritor") as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sentencia)
