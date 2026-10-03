"""Conexiones a PostgreSQL por rol (spec técnico §4.3).

Cada perfil de uso tiene su propio rol de base de datos, con permisos mínimos:

- ``lector_admin`` → ``gym_lector_admin``: lectura del perfil Administrador y de ``auth.py``.
- ``lector_socio`` → ``gym_lector_socio``: lectura del perfil Socio (solo vistas de ``socio_api``).
- ``escritor`` → ``gym_escritor``: exclusivo de ``app/ops/ejecucion.py``.
- ``superusuario``: solo para ``scripts/reset_db.py`` y los tests.

No se usa un pool: cada operación abre y cierra su conexión, lo que simplifica restaurar la
base desde la plantilla (no quedan conexiones abiertas) y el volumen de uso es bajo.
"""

from __future__ import annotations

from typing import Literal

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings

Rol = Literal["lector_admin", "lector_socio", "escritor", "superusuario"]

_USUARIOS = {
    "lector_admin": "gym_lector_admin",
    "lector_socio": "gym_lector_socio",
    "escritor": "gym_escritor",
}


def conninfo(rol: Rol, dbname: str | None = None) -> str:
    """Arma la cadena de conexión para un rol.

    Args:
        rol: rol lógico de la aplicación.
        dbname: base a la que conectarse; por defecto ``DB_NAME`` del ``.env``.
    """
    s = get_settings()
    if rol == "superusuario":
        user, password = s.postgres_user, s.postgres_password
    else:
        user = _USUARIOS[rol]
        password = {
            "lector_admin": s.db_pass_lector_admin,
            "lector_socio": s.db_pass_lector_socio,
            "escritor": s.db_pass_escritor,
        }[rol]
    return psycopg.conninfo.make_conninfo(
        host=s.db_host,
        port=s.db_port,
        dbname=dbname or s.db_name,
        user=user,
        password=password,
        connect_timeout=5,
        options="-c TimeZone=America/Argentina/Buenos_Aires",
        application_name=f"gimnasio-agente-{rol}",
    )


def conectar(rol: Rol, dbname: str | None = None, autocommit: bool = False) -> psycopg.Connection:
    """Abre una conexión con el rol indicado; las filas se devuelven como ``dict``.

    Usar como context manager (``with conectar("lector_admin") as conn: ...``): al salir
    hace commit (o rollback si hubo una excepción) y cierra la conexión.
    """
    return psycopg.connect(conninfo(rol, dbname), autocommit=autocommit, row_factory=dict_row)
