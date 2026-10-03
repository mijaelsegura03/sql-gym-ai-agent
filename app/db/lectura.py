"""Validación y ejecución de la SQL de lectura que genera el LLM (spec técnico §6.2 y §6.3).

Hay tres barreras independientes (RNF-01, RNF-02):

1. :func:`validar_sql`: con sqlglot, exige una única sentencia ``SELECT`` (o ``WITH … SELECT``),
   sin escrituras ni DDL, solo sobre las relaciones permitidas para el perfil y solo con funciones
   de una lista permitida (``set_config``, ``current_setting``, ``pg_*``, etc. quedan afuera).
2. El rol de la base: ``gym_lector_admin`` o ``gym_lector_socio``, ambos de solo lectura; el del
   socio solo ve las vistas de ``socio_api``.
3. La transacción ``READ ONLY``.

Los errores (de validación o de la base) se devuelven como texto en :class:`ResultadoLectura`
para que ``generar_sql`` corrija la consulta, sin cortar el proceso (RNF-05).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

import psycopg
import sqlglot
from pydantic import BaseModel
from psycopg.rows import tuple_row
from sqlglot import exp

from app.db.conexion import conectar

MAX_FILAS = 50  # RF-04

TABLAS_PUBLIC = {
    "sede", "sala", "socio", "empleado", "apto_medico", "actividad", "plan", "plan_actividad", "membresia",
    "pago", "clase", "sesion_clase", "reserva", "acceso", "ejercicio", "rutina", "rutina_ejercicio",
}
VISTAS_SOCIO = {
    "sede", "sala", "actividad", "plan", "plan_actividad", "ejercicio", "clase", "sesion_clase",
    "ocupacion_sesion", "mi_socio", "mis_aptos", "mis_membresias", "mis_pagos", "mis_reservas",
    "mis_accesos", "mis_rutinas", "mis_rutina_ejercicios",
}

# Funciones permitidas, por clase de sqlglot (funciones que sqlglot reconoce)...
_FUNCIONES_CLASE = {
    # agregados y ventanas
    "Count", "Sum", "Avg", "Min", "Max", "ArrayAgg", "GroupConcat", "LogicalOr", "LogicalAnd", "Stddev",
    "StddevPop", "StddevSamp", "Variance", "Median", "PercentileCont", "PercentileDisc", "RowNumber", "Rank",
    "DenseRank", "Lag", "Lead", "FirstValue", "LastValue", "NthValue", "Ntile", "PercentRank", "CumeDist",
    "CountIf", "Filter",
    # fecha y hora
    "CurrentDate", "CurrentTimestamp", "CurrentTime", "Localtimestamp", "Localtime", "Date", "DateTrunc",
    "TimestampTrunc", "TimeTrunc", "Extract", "DatePart", "DateAdd", "DateSub", "DateDiff", "TimeToStr",
    "StrToDate", "StrToTime", "UnixToTime", "TsOrDsToDate", "JustifyInterval", "JustifyDays", "JustifyHours",
    "MakeInterval", "DateFromParts", "TimestampFromParts", "Interval", "AtTimeZone", "LastDay", "ToChar",
    "DayOfWeek", "DayOfWeekIso", "DayOfMonth", "DayOfYear", "Month", "Year", "Week", "Quarter", "Day",
    "GenerateSeries", "ExplodingGenerateSeries", "GenerateDateArray",
    # texto
    "Lower", "Upper", "Trim", "Length", "Concat", "ConcatWs", "Substring", "Replace", "Initcap", "Left",
    "Right", "SplitPart", "StrPosition", "Lpad", "Rpad", "Reverse", "RegexpLike", "RegexpReplace", "Chr",
    "Ascii", "Unicode", "Translate", "Repeat",
    # matemáticas y condicionales
    "Abs", "Round", "Floor", "Ceil", "Trunc", "Pow", "Sqrt", "Sign", "Mod", "Ln", "Log", "Exp", "Greatest",
    "Least", "Coalesce", "Nullif", "If", "Case", "Cast", "TryCast", "Exists", "Array", "ArraySize",
    "ArrayContains", "Explode", "Unnest", "Struct", "Bracket",
}
# ...y por nombre, para las que sqlglot deja como función anónima
_FUNCIONES_NOMBRE = {
    "age", "make_date", "make_time", "make_timestamp", "make_interval", "date_part", "to_char", "to_date",
    "to_timestamp", "to_number", "generate_series", "isfinite", "justify_days", "justify_hours", "unaccent",
    "array_length", "cardinality", "string_agg", "btrim", "ltrim", "rtrim", "char_length", "format",
    "width_bucket", "div", "every", "bool_and", "bool_or", "percentile_cont", "date_bin", "array_position",
}
_FUNCIONES_PROHIBIDAS = ("set_config", "current_setting", "pg_", "lo_", "dblink", "query_to_xml", "copy")

# Nodos que nunca pueden aparecer en una consulta de lectura
_NODOS_PROHIBIDOS = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Copy, exp.Set, exp.Into, exp.Lock, exp.TruncateTable, exp.Grant, exp.Use, exp.Transaction,
    exp.Commit, exp.Rollback,
)


class ErrorValidacionSQL(ValueError):
    """La SQL generada no cumple las reglas del §6.2."""


class ResultadoLectura(BaseModel):
    """Resultado de :func:`ejecutar_lectura`."""

    sql: str
    columnas: list[str] = []
    filas: list[dict[str, Any]] = []
    total_filas: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _nombre_funcion(f: exp.Func) -> str:
    return f.name if isinstance(f, exp.Anonymous) else type(f).__name__


def validar_sql(sql: str, perfil: Literal["socio", "admin"]) -> str:
    """Valida la SQL generada por el LLM para el perfil indicado.

    Returns:
        La SQL normalizada (sin ``;`` final), lista para envolver en el ``LIMIT``.

    Raises:
        ErrorValidacionSQL: con un mensaje que explica qué regla no se cumple.
    """
    texto = (sql or "").strip().rstrip(";").strip()
    if not texto:
        raise ErrorValidacionSQL("La consulta está vacía.")
    try:
        sentencias = [s for s in sqlglot.parse(texto, read="postgres") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise ErrorValidacionSQL(f"La consulta no es SQL válido de PostgreSQL: {str(e).splitlines()[0]}") from None
    if len(sentencias) != 1:
        raise ErrorValidacionSQL("Se permite exactamente una sentencia.")
    arbol = sentencias[0]
    if not isinstance(arbol, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
        raise ErrorValidacionSQL("Solo se permiten consultas SELECT (o WITH … SELECT).")

    for nodo in arbol.walk():
        if isinstance(nodo, _NODOS_PROHIBIDOS):
            raise ErrorValidacionSQL(f"La consulta contiene una operación no permitida ({type(nodo).__name__}).")

    # Funciones: lista permitida
    for f in arbol.find_all(exp.Func):
        nombre = _nombre_funcion(f)
        bajo = nombre.lower()
        if any(bajo.startswith(p) for p in _FUNCIONES_PROHIBIDAS):
            raise ErrorValidacionSQL(f"La función {nombre} no está permitida.")
        if isinstance(f, exp.Anonymous):
            if bajo not in _FUNCIONES_NOMBRE:
                raise ErrorValidacionSQL(f"La función {nombre} no está en la lista de funciones permitidas.")
        elif nombre not in _FUNCIONES_CLASE and not isinstance(f, (exp.AggFunc, exp.Binary, exp.Paren)):
            raise ErrorValidacionSQL(f"La función {f.sql_name()} no está en la lista de funciones permitidas.")

    # Relaciones permitidas según el perfil
    ctes = {c.alias_or_name.lower() for c in arbol.find_all(exp.CTE)}
    for t in arbol.find_all(exp.Table):
        if isinstance(t.this, exp.Func):  # función de tabla, p. ej. generate_series (validada arriba)
            continue
        nombre, esquema = t.name.lower(), (t.db or "").lower()
        if not nombre:
            continue
        if not esquema and nombre in ctes:
            continue
        if t.catalog:
            raise ErrorValidacionSQL("No se permite referenciar otras bases de datos.")
        if perfil == "admin":
            permitida = (esquema in ("", "public") and nombre in TABLAS_PUBLIC) or (esquema, nombre) == ("agente", "auditoria")
        else:
            permitida = esquema in ("", "socio_api") and nombre in VISTAS_SOCIO
        if not permitida:
            raise ErrorValidacionSQL(f"La relación {t.sql(dialect='postgres')} no existe o no está permitida para este perfil.")
    return texto


def _convertir(valor: Any) -> Any:
    """Convierte los tipos de Postgres a tipos simples para la salida (§6.3 paso 5)."""
    if isinstance(valor, Decimal):
        return round(float(valor), 2)
    if isinstance(valor, (dt.datetime, dt.date, dt.time)):
        return valor.isoformat()
    if isinstance(valor, dt.timedelta):
        return str(valor)
    return valor


def _nombres_unicos(nombres: list[str]) -> list[str]:
    """Renombra columnas repetidas (``nombre``, ``nombre_2``) para no perder valores en los dict."""
    vistos: dict[str, int] = {}
    salida = []
    for n in nombres:
        vistos[n] = vistos.get(n, 0) + 1
        salida.append(n if vistos[n] == 1 else f"{n}_{vistos[n]}")
    return salida


def ejecutar_lectura(sql: str, usuario) -> ResultadoLectura:
    """Valida y ejecuta una consulta de lectura con el rol del perfil del usuario.

    Pasos (§6.3): transacción ``READ ONLY`` con el rol del perfil; si es socio, fija
    ``app.socio_id``; ejecuta con ``LIMIT 51``; si vuelven 51 filas, cuenta el total y devuelve
    las primeras 50 (RF-04). Nunca lanza excepciones por errores de la consulta: los devuelve en
    ``error`` para el reintento.

    Args:
        sql: consulta generada por el LLM.
        usuario: :class:`app.auth.Usuario` de la sesión.
    """
    try:
        consulta = validar_sql(sql, usuario.perfil)
    except ErrorValidacionSQL as e:
        return ResultadoLectura(sql=sql, error=f"Consulta rechazada por el validador: {e}")

    rol = "lector_admin" if usuario.es_admin else "lector_socio"
    try:
        with conectar(rol) as conn:
            conn.read_only = True
            with conn.transaction():
                if not usuario.es_admin:
                    conn.execute("SELECT set_config('app.socio_id', %s, true)", (str(usuario.id),))
                cur = conn.cursor(row_factory=tuple_row)
                cur.execute(f"SELECT * FROM ({consulta}) AS q LIMIT {MAX_FILAS + 1}")  # type: ignore[arg-type]
                columnas = _nombres_unicos([d.name for d in (cur.description or [])])
                filas = cur.fetchall()
                total = len(filas)
                if total > MAX_FILAS:
                    total = conn.execute(f"SELECT count(*) AS n FROM ({consulta}) AS q").fetchone()["n"]  # type: ignore[arg-type]
                    filas = filas[:MAX_FILAS]
    except psycopg.Error as e:
        mensaje = (getattr(e, "diag", None) and e.diag.message_primary) or str(e)
        return ResultadoLectura(sql=consulta, error=f"Error de la base de datos: {mensaje}")
    return ResultadoLectura(
        sql=consulta,
        columnas=columnas,
        filas=[{c: _convertir(v) for c, v in zip(columnas, f)} for f in filas],
        total_filas=total,
    )
