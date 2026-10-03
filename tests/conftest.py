"""Fixtures compartidas de los tests.

Los tests marcados con ``@pytest.mark.db`` necesitan el contenedor levantado y la base cargada
(``python start.py --sin-ui`` o ``python scripts/reset_db.py``). Si la base no responde, se saltean.
Los marcados con ``@pytest.mark.llm`` llaman a Gemini y no corren por defecto
(``pytest -m llm`` para correrlos). Las pruebas que escriben restauran la base al terminar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_DB_OK: bool | None = None


def _db_disponible() -> bool:
    try:
        from app.db.conexion import conectar

        with conectar("superusuario") as conn:
            conn.execute("SELECT 1 FROM socio LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def pytest_runtest_setup(item: pytest.Item) -> None:
    global _DB_OK
    if item.get_closest_marker("db"):
        if _DB_OK is None:
            _DB_OK = _db_disponible()
        if not _DB_OK:
            pytest.skip("PostgreSQL no disponible (ejecutá `python start.py --sin-ui`)")
    if item.get_closest_marker("llm"):
        from app.config import get_settings

        if not get_settings().google_api_key:
            pytest.skip("Falta GOOGLE_API_KEY")


@pytest.fixture
def su():
    """Conexión de superusuario a la base de trabajo (para preparar y verificar)."""
    from app.db.conexion import conectar

    with conectar("superusuario") as conn:
        yield conn


@pytest.fixture
def base_restaurada():
    """Restaura la base desde la plantilla después del test (para tests que escriben)."""
    yield
    from scripts.reset_db import restaurar

    restaurar()
