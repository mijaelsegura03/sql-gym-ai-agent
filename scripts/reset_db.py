"""Crea la base del gimnasio desde cero (spec técnico §4.2).

1. Crea la base plantilla ``gimnasio_template`` y ejecuta, en orden y con el superusuario:
   ``gimnasio_schema.sql``, ``gimnasio_datos.sql``, ``agente_schema.sql`` y ``roles.sql``
   (las contraseñas de los roles se pasan como parámetros de sesión, desde el ``.env``).
2. Clona la base de trabajo con ``CREATE DATABASE gimnasio TEMPLATE gimnasio_template``.

La función :func:`restaurar` vuelve a clonar la base de trabajo desde la plantilla en menos de
un segundo; la usan la evaluación (antes de cada caso de escritura, RF-74) y los tests.

Uso::

    python scripts/reset_db.py            # plantilla + base de trabajo
    python scripts/reset_db.py --restaurar  # solo recrea la base de trabajo desde la plantilla
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

import psycopg  # noqa: E402
from psycopg import sql  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db.conexion import conectar  # noqa: E402

PLANTILLA = "gimnasio_template"
SCRIPTS = [
    RAIZ / "data" / "gimnasio_schema.sql",
    RAIZ / "data" / "gimnasio_datos.sql",
    RAIZ / "data" / "agente_schema.sql",
    RAIZ / "data" / "roles.sql",
]
TABLAS = [
    "sede", "sala", "actividad", "plan", "plan_actividad", "empleado", "socio", "apto_medico",
    "membresia", "pago", "clase", "sesion_clase", "reserva", "acceso", "ejercicio", "rutina",
    "rutina_ejercicio",
]


class ErrorScript(RuntimeError):
    """Falló uno de los scripts SQL; el mensaje indica el archivo y el error."""


def _recrear_db(conn: psycopg.Connection, nombre: str, plantilla: str | None = None) -> None:
    """Borra (cortando las conexiones abiertas) y vuelve a crear una base."""
    conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(nombre)))
    if plantilla:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {}").format(sql.Identifier(nombre), sql.Identifier(plantilla))
        )
    else:
        conn.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(nombre)))


def crear_plantilla() -> None:
    """Crea ``gimnasio_template`` y ejecuta los 4 scripts SQL en orden.

    Raises:
        ErrorScript: si un script falla.
    """
    s = get_settings()
    with conectar("superusuario", dbname="postgres", autocommit=True) as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(s.db_name)))
        _recrear_db(conn, PLANTILLA)

    with conectar("superusuario", dbname=PLANTILLA, autocommit=True) as conn:
        # Contraseñas de los roles para roles.sql (parámetros de sesión, no quedan en el archivo)
        for clave, valor in [
            ("gym.pass_lector_admin", s.db_pass_lector_admin),
            ("gym.pass_lector_socio", s.db_pass_lector_socio),
            ("gym.pass_escritor", s.db_pass_escritor),
        ]:
            conn.execute("SELECT set_config(%s, %s, false)", (clave, valor))
        for script in SCRIPTS:
            try:
                conn.execute(script.read_text(encoding="utf-8"))  # type: ignore[arg-type]
            except psycopg.Error as e:
                raise ErrorScript(f"Falló {script.relative_to(RAIZ)}: {e}".strip()) from None


def restaurar() -> float:
    """Recrea la base de trabajo desde la plantilla. Devuelve los segundos que tardó."""
    t0 = time.perf_counter()
    with conectar("superusuario", dbname="postgres", autocommit=True) as conn:
        _recrear_db(conn, get_settings().db_name, PLANTILLA)
    return time.perf_counter() - t0


def contar_registros() -> dict[str, int]:
    """Cantidad de filas por tabla del esquema del gimnasio (verificación rápida de R-03)."""
    with conectar("superusuario") as conn:
        return {
            t: conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(sql.Identifier(t))).fetchone()["n"]
            for t in TABLAS
        }


def reset() -> dict[str, int]:
    """Plantilla + base de trabajo. Devuelve el conteo de registros por tabla."""
    crear_plantilla()
    restaurar()
    return contar_registros()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--restaurar", action="store_true", help="solo recrear la base de trabajo desde la plantilla")
    args = parser.parse_args()
    try:
        if args.restaurar:
            print(f"Base restaurada desde la plantilla en {restaurar():.2f} s")
            return 0
        conteo = reset()
    except ErrorScript as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except psycopg.OperationalError as e:
        print(f"ERROR: no se pudo conectar a PostgreSQL ({e}). ¿Está levantado el contenedor?", file=sys.stderr)
        return 1
    for tabla, n in conteo.items():
        print(f"  {tabla:<17} {n:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
