"""Exploración del esquema para el agente: ``describir_tabla`` y ``valores_columna`` (spec técnico §6.4; RF-24, DT-11).

La SQL la escribe este módulo, no el LLM. El LLM solo elige nombres, que se validan contra las mismas
listas que usa :func:`app.db.lectura.validar_sql` y se insertan como identificadores
(``psycopg.sql.Identifier``), nunca como texto. Las consultas corren igual que ``ejecutar_lectura``:
rol del perfil, transacción ``READ ONLY`` y, para el socio, ``app.socio_id`` fijado, así los conteos y
los valores salen solo de las filas que el perfil puede ver (RNF-01).

Los errores se devuelven como texto en :class:`ResultadoExploracion` para que el agente corrija el
pedido en el paso siguiente (RNF-05).
"""

from __future__ import annotations

import psycopg
from psycopg import sql
from pydantic import BaseModel

from app.db.conexion import conectar
from app.db.lectura import TABLAS_PUBLIC, VISTAS_SOCIO

MAX_VALORES = 30


class ResultadoExploracion(BaseModel):
    """Resultado de :func:`describir_tabla` o :func:`valores_columna`."""

    texto: str = ""
    resumen: str = ""                # para el detalle: "12 columnas", "3 valores"
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class _ErrorExploracion(ValueError):
    pass


def _permitidas(usuario) -> list[tuple[str, str]]:
    if usuario.es_admin:
        return [("public", t) for t in sorted(TABLAS_PUBLIC)] + [("agente", "auditoria")]
    return [("socio_api", v) for v in sorted(VISTAS_SOCIO)]


def _resolver(tabla: str, usuario) -> tuple[str, str]:
    """``(esquema, nombre)`` de una tabla o vista permitida para el perfil; si no, ``_ErrorExploracion``."""
    texto = (tabla or "").strip().strip('"').lower()
    esquema, _, nombre = texto.rpartition(".")
    for permitida in _permitidas(usuario):
        if nombre == permitida[1] and esquema in ("", permitida[0]):
            return permitida
    nombres = ", ".join(n if e in ("public", "socio_api") else f"{e}.{n}" for e, n in _permitidas(usuario))
    raise _ErrorExploracion(f"La tabla {tabla!r} no existe o no está permitida para este perfil. "
                            f"Tablas disponibles: {nombres}.")


def _nombre(esquema: str, nombre: str) -> str:
    return nombre if esquema in ("public", "socio_api") else f"{esquema}.{nombre}"


def _conexion(usuario):
    conn = conectar("lector_admin" if usuario.es_admin else "lector_socio")
    conn.read_only = True
    return conn


def _preparar(conn, usuario) -> None:
    """Dentro de la transacción ``READ ONLY``: fija el socio de la sesión para las vistas de ``socio_api``."""
    if not usuario.es_admin:
        conn.execute("SELECT set_config('app.socio_id', %s, true)", (str(usuario.id),))


def _columnas(conn, esquema: str, nombre: str) -> list[dict]:
    return conn.execute(
        "SELECT column_name AS columna, data_type AS tipo, is_nullable = 'YES' AS nulos "
        "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
        (esquema, nombre)).fetchall()


def _ejecutar(funcion, *args) -> ResultadoExploracion:
    try:
        return funcion(*args)
    except _ErrorExploracion as e:
        return ResultadoExploracion(error=str(e))
    except psycopg.Error as e:
        mensaje = (getattr(e, "diag", None) and e.diag.message_primary) or str(e)
        return ResultadoExploracion(error=f"Error de la base de datos: {mensaje}")


def describir_tabla(tabla: str, usuario) -> ResultadoExploracion:
    """Columnas (tipo y nulos), claves foráneas y filas visibles de una tabla o vista permitida."""
    return _ejecutar(_describir, tabla, usuario)


def _describir(tabla: str, usuario) -> ResultadoExploracion:
    esquema, nombre = _resolver(tabla, usuario)
    with _conexion(usuario) as conn:
        with conn.transaction():
            _preparar(conn, usuario)
            columnas = _columnas(conn, esquema, nombre)
            claves = conn.execute(
                "SELECT a.attname AS columna, ref.relname AS referencia "
                "FROM pg_constraint c "
                "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
                "JOIN pg_class ref ON ref.oid = c.confrelid "
                "WHERE c.contype = 'f' AND c.conrelid = to_regclass(%s) ORDER BY a.attnum",
                (f"{esquema}.{nombre}",)).fetchall()
            filas = conn.execute(sql.SQL("SELECT count(*) AS n FROM {}").format(
                sql.Identifier(esquema, nombre))).fetchone()["n"]
    lineas = [f"Tabla {_nombre(esquema, nombre)} ({filas} fila{'s' if filas != 1 else ''} visibles)", "Columnas:"]
    lineas += [f"- {c['columna']}: {c['tipo']}" + (" (admite NULL)" if c["nulos"] else "") for c in columnas]
    if claves:
        lineas += ["Claves foráneas:"] + [f"- {k['columna']} → {k['referencia']}" for k in claves]
    return ResultadoExploracion(texto="\n".join(lineas),
                                resumen=f"{len(columnas)} columna{'s' if len(columnas) != 1 else ''}")


def valores_columna(tabla: str, columna: str, usuario) -> ResultadoExploracion:
    """Hasta :data:`MAX_VALORES` valores distintos de una columna, con su cantidad de filas."""
    return _ejecutar(_valores, tabla, columna, usuario)


def _valores(tabla: str, columna: str, usuario) -> ResultadoExploracion:
    esquema, nombre = _resolver(tabla, usuario)
    pedida = (columna or "").strip().strip('"').lower()
    with _conexion(usuario) as conn:
        with conn.transaction():
            _preparar(conn, usuario)
            columnas = [c["columna"] for c in _columnas(conn, esquema, nombre)]
            if pedida not in columnas:
                raise _ErrorExploracion(f"La columna {columna!r} no existe en {_nombre(esquema, nombre)}. "
                                        f"Columnas: {', '.join(columnas)}.")
            tabla_sql, col_sql = sql.Identifier(esquema, nombre), sql.Identifier(pedida)
            valores = conn.execute(
                sql.SQL("SELECT {c}::text AS valor, count(*) AS n FROM {t} GROUP BY 1 "
                        "ORDER BY 2 DESC, 1 NULLS LAST LIMIT {lim}").format(
                    c=col_sql, t=tabla_sql, lim=sql.Literal(MAX_VALORES))).fetchall()
            distintos = conn.execute(sql.SQL("SELECT count(DISTINCT {c}) AS n FROM {t}").format(
                c=col_sql, t=tabla_sql)).fetchone()["n"]
    if any(v["valor"] is None for v in valores):
        distintos += 1  # count(DISTINCT) no cuenta NULL
    encabezado = f"Valores de {_nombre(esquema, nombre)}.{pedida} ({distintos} distinto{'s' if distintos != 1 else ''})"
    if distintos > len(valores):
        encabezado += f"; se muestran los {len(valores)} más frecuentes"
    lineas = [encabezado + ":"] + [f"- {'NULL' if v['valor'] is None else v['valor']}: {v['n']}" for v in valores]
    if not valores:
        lineas.append("(la tabla no tiene filas visibles)")
    return ResultadoExploracion(texto="\n".join(lineas),
                                resumen=f"{distintos} valor{'es' if distintos != 1 else ''}")
