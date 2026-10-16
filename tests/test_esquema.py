"""Exploración del esquema: ``describir_tabla`` y ``valores_columna`` (spec técnico §6.4, §12; T-42; RF-24, RNF-01)."""

from __future__ import annotations

import pytest

from app.auth import Usuario
from app.db.esquema import MAX_VALORES, describir_tabla, valores_columna

pytestmark = pytest.mark.db

ADMIN = Usuario(perfil="admin", id=1, nombre="Admin", sede_alcance=None, seudonimo="a")


def _socio(su) -> Usuario:
    fila = su.execute("SELECT s.id FROM socio s WHERE EXISTS (SELECT 1 FROM pago p JOIN membresia m ON m.id = p.membresia_id "
                      "WHERE m.socio_id = s.id) ORDER BY s.id LIMIT 1").fetchone()
    return Usuario(perfil="socio", id=fila["id"], nombre="Socio", sede_alcance=1, seudonimo="s")


def test_describir_tabla_admin_con_columnas_y_claves():
    r = describir_tabla("socio", ADMIN)
    assert r.ok and r.texto.startswith("Tabla socio (")
    assert "- dni: character varying" in r.texto and "- email: character varying (admite NULL)" in r.texto
    assert "- sede_principal_id → sede" in r.texto  # claves foráneas desde pg_constraint
    assert describir_tabla("agente.auditoria", ADMIN).ok and describir_tabla("PUBLIC.Membresia", ADMIN).ok


def test_el_socio_solo_explora_sus_vistas(su):
    socio = _socio(su)
    for tabla in ("socio", "public.pago", "agente.auditoria", "pg_catalog.pg_authid"):
        r = describir_tabla(tabla, socio)
        assert not r.ok and "no está permitida" in r.error and "mis_pagos" in r.error
        assert not valores_columna(tabla, "id", socio).ok
    assert describir_tabla("mis_pagos", socio).ok and describir_tabla("socio_api.mis_pagos", socio).ok


def test_conteos_y_valores_del_socio_solo_de_sus_filas(su):
    socio = _socio(su)
    propios = su.execute("SELECT count(*) AS n FROM pago p JOIN membresia m ON m.id = p.membresia_id "
                         "WHERE m.socio_id = %s", (socio.id,)).fetchone()["n"]
    assert f"({propios} fila" in describir_tabla("mis_pagos", socio).texto
    r = valores_columna("mis_pagos", "estado", socio)
    assert r.ok and sum(int(l.rsplit(": ", 1)[1]) for l in r.texto.splitlines()[1:]) == propios


def test_valores_de_un_campo_enumerado():
    r = valores_columna("membresia", "estado", ADMIN)
    assert r.ok and r.texto.startswith("Valores de membresia.estado (")
    assert "- activa: " in r.texto and "- vencida: " in r.texto and r.resumen.endswith("valores")


def test_valores_limitados_a_los_mas_frecuentes():
    r = valores_columna("socio", "dni", ADMIN)
    assert r.ok and f"se muestran los {MAX_VALORES} más frecuentes" in r.texto
    assert len(r.texto.splitlines()) == MAX_VALORES + 1


@pytest.mark.parametrize("columna", ["no_existe", "estado; DROP TABLE socio", 'estado" FROM socio --'])
def test_columna_inexistente_o_inyeccion(columna, su):
    r = valores_columna("socio", columna, ADMIN)
    assert not r.ok and "no existe" in r.error and "Columnas: id, dni" in r.error
    assert su.execute("SELECT count(*) AS n FROM socio").fetchone()["n"] > 0
