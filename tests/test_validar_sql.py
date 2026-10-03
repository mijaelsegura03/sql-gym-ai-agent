"""Validación de la SQL de lectura (spec técnico §6.2) y ejecución (§6.3)."""

from __future__ import annotations

import pytest

from app.db.lectura import ErrorValidacionSQL, ejecutar_lectura, validar_sql

ACEPTADAS_ADMIN = [
    "SELECT s.nombre, count(*) FROM socio s JOIN sede se ON se.id = s.sede_principal_id GROUP BY s.nombre",
    "WITH ult AS (SELECT socio_id, max(fecha_vencimiento) v FROM apto_medico GROUP BY socio_id) "
    "SELECT so.dni, ult.v FROM ult JOIN socio so ON so.id = ult.socio_id WHERE ult.v < CURRENT_DATE",
    "SELECT sum(monto) FROM pago WHERE metodo = 'mercadopago' AND estado = 'aprobado' "
    "AND fecha_pago >= date_trunc('month', CURRENT_DATE) - INTERVAL '1 month' AND fecha_pago < date_trunc('month', CURRENT_DATE)",
    "SELECT extract(isodow FROM fecha), to_char(fecha, 'DD/MM/YYYY'), age(CURRENT_DATE, fecha) FROM sesion_clase",
    "SELECT coalesce(nullif(email, ''), '-'), greatest(1, 2), least(1, 2), cast(precio AS int), round(precio::numeric, 2) FROM socio, plan;",
    "SELECT * FROM agente.auditoria",
    "SELECT d::date FROM generate_series(CURRENT_DATE - 6, CURRENT_DATE, INTERVAL '1 day') AS d",
    "SELECT nombre FROM socio UNION SELECT nombre FROM empleado",
    "SELECT row_number() OVER (ORDER BY id), lower(nombre) FROM public.socio WHERE nombre ILIKE '%juan%'",
]

RECHAZADAS = [
    "INSERT INTO sede (nombre, direccion) VALUES ('a','b')",
    "UPDATE socio SET estado='baja'",
    "DELETE FROM acceso",
    "DROP TABLE socio",
    "CREATE TABLE x (a int)",
    "ALTER TABLE socio ADD COLUMN x int",
    "TRUNCATE acceso",
    "SELECT 1; SELECT 2",
    "SELECT 1; DELETE FROM socio",
    "SELECT * INTO copia FROM socio",
    "SELECT set_config('app.socio_id', '5', true)",
    "SELECT current_setting('app.socio_id')",
    "SELECT pg_sleep(10)",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT * FROM socio FOR UPDATE",
    "COPY socio TO STDOUT",
    "SET statement_timeout = 0",
    "WITH x AS (DELETE FROM acceso RETURNING *) SELECT * FROM x",
    "SELECT * FROM pg_catalog.pg_roles",
    "SELECT * FROM information_schema.tables",
    "",
]


@pytest.mark.parametrize("sql", ACEPTADAS_ADMIN)
def test_acepta_admin(sql):
    assert validar_sql(sql, "admin")


@pytest.mark.parametrize("sql", RECHAZADAS)
@pytest.mark.parametrize("perfil", ["admin", "socio"])
def test_rechaza(sql, perfil):
    with pytest.raises(ErrorValidacionSQL):
        validar_sql(sql, perfil)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM socio",
    "SELECT * FROM public.socio",
    "SELECT * FROM pago",
    "SELECT * FROM agente.auditoria",
    "SELECT nombre FROM mi_socio UNION SELECT nombre FROM empleado",
])
def test_socio_no_accede_a_public(sql):
    with pytest.raises(ErrorValidacionSQL):
        validar_sql(sql, "socio")


@pytest.mark.parametrize("sql", [
    "SELECT m.fecha_fin, p.nombre FROM mis_membresias m JOIN plan p ON p.id = m.plan_id ORDER BY m.fecha_fin DESC",
    "SELECT count(*) FROM mis_reservas WHERE asistio AND estado = 'confirmada'",
    "SELECT o.lugares_disponibles FROM ocupacion_sesion o JOIN socio_api.sesion_clase s ON s.id = o.sesion_id",
])
def test_acepta_socio(sql):
    assert validar_sql(sql, "socio")


def test_quita_punto_y_coma_final():
    assert validar_sql("SELECT 1 FROM sede;", "admin") == "SELECT 1 FROM sede"


# ---------------------------------------------------------------------------
# Ejecución (requiere la base)
# ---------------------------------------------------------------------------
@pytest.fixture
def admin():
    from app.auth import Usuario

    return Usuario(perfil="admin", id=15, nombre="Admin", sede_alcance=None, seudonimo="x")


@pytest.mark.db
def test_mas_de_50_filas_devuelve_50_y_el_total(admin):
    r = ejecutar_lectura("SELECT id, dni FROM socio ORDER BY id", admin)
    assert r.ok and len(r.filas) == 50 and r.total_filas == 120
    assert r.filas[0] == {"id": 1, "dni": r.filas[0]["dni"]}


@pytest.mark.db
def test_errores_de_la_base_se_devuelven_como_texto(admin):
    r = ejecutar_lectura("SELECT columna_que_no_existe FROM socio", admin)
    assert not r.ok and "columna_que_no_existe" in r.error


@pytest.mark.db
def test_errores_del_validador_se_devuelven_como_texto(admin):
    r = ejecutar_lectura("DELETE FROM socio", admin)
    assert not r.ok and "validador" in r.error


@pytest.mark.db
def test_socio_solo_ve_lo_suyo(su):
    from app.auth import Usuario

    socio_id = su.execute("SELECT socio_id FROM membresia GROUP BY socio_id HAVING count(*) > 2 LIMIT 1").fetchone()["socio_id"]
    u = Usuario(perfil="socio", id=socio_id, nombre="x", sede_alcance=1, seudonimo="x")
    r = ejecutar_lectura("SELECT DISTINCT socio_id FROM mis_membresias", u)
    assert r.ok and r.filas == [{"socio_id": socio_id}]


@pytest.mark.db
def test_tipos_convertidos_y_columnas_repetidas(admin):
    r = ejecutar_lectura("SELECT s.nombre, p.nombre, p.precio, CURRENT_DATE AS hoy FROM socio s, plan p LIMIT 1", admin)
    assert r.ok and r.columnas == ["nombre", "nombre_2", "precio", "hoy"]
    assert isinstance(r.filas[0]["precio"], float) and isinstance(r.filas[0]["hoy"], str)
