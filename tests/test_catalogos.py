"""Los catálogos de text-to-SQL cubren el esquema y sus ejemplos se ejecutan (T-11)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.auth import Usuario
from app.db.lectura import TABLAS_PUBLIC, VISTAS_SOCIO, ejecutar_lectura

CATALOGOS = Path(__file__).resolve().parent.parent / "app" / "db" / "catalogos"


def _ejemplos(nombre: str) -> list[str]:
    texto = (CATALOGOS / nombre).read_text(encoding="utf-8")
    bloque = re.search(r"```sql\n(.*?)```", texto, re.S).group(1)
    sentencias = []
    for parte in bloque.split(";"):
        lineas = [l for l in parte.strip().splitlines() if not l.strip().startswith("--")]
        if lineas:
            sentencias.append("\n".join(lineas))
    return sentencias


def test_admin_md_tiene_todas_las_tablas():
    texto = (CATALOGOS / "admin.md").read_text(encoding="utf-8")
    for t in TABLAS_PUBLIC | {"agente.auditoria"}:
        assert f"### {t} " in texto or f"### {t}\n" in texto, t


def test_socio_md_tiene_todas_las_vistas_y_ninguna_tabla_privada():
    texto = (CATALOGOS / "socio.md").read_text(encoding="utf-8")
    for v in VISTAS_SOCIO:
        assert f"### {v} " in texto or f"### {v}\n" in texto, v
    for privada in TABLAS_PUBLIC - VISTAS_SOCIO:
        # ni como sección del catálogo ni usada en una consulta de ejemplo
        assert f"### {privada}" not in texto, privada
        assert not re.search(rf"\b(FROM|JOIN)\s+{privada}\b", texto, re.I), privada
    assert "public." not in texto


@pytest.mark.parametrize("archivo", ["admin.md", "socio.md"])
def test_semantica_documentada(archivo):
    texto = (CATALOGOS / archivo).read_text(encoding="utf-8").lower()
    for concepto in ["membresía vigente", "apto vigente", "asistencia"]:
        assert concepto in texto
    if archivo == "admin.md":
        assert "moroso" in texto and "pago válido" in texto


@pytest.mark.parametrize("archivo", ["admin.md", "socio.md"])
def test_entre_6_y_8_ejemplos(archivo):
    assert 6 <= len(_ejemplos(archivo)) <= 8


@pytest.mark.db
@pytest.mark.parametrize("archivo,perfil", [("admin.md", "admin"), ("socio.md", "socio")])
def test_ejemplos_se_ejecutan_con_el_rol(su, archivo, perfil):
    if perfil == "admin":
        usuario = Usuario(perfil="admin", id=15, nombre="x", sede_alcance=None, seudonimo="x")
    else:
        sid = su.execute("""SELECT s.id FROM socio s WHERE EXISTS (SELECT 1 FROM rutina r WHERE r.socio_id=s.id AND r.activa)
                            ORDER BY s.id LIMIT 1""").fetchone()["id"]
        usuario = Usuario(perfil="socio", id=sid, nombre="x", sede_alcance=1, seudonimo="x")
    for sql in _ejemplos(archivo):
        r = ejecutar_lectura(sql, usuario)
        assert r.ok, f"{r.error}\n{sql}"
