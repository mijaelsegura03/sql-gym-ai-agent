"""Registro de auditoría de las operaciones de escritura (RF-49, spec técnico §4.5 y §7.3).

Se registran las operaciones ejecutadas (dentro de su misma transacción, ver ``ejecucion.py``) y,
en una transacción propia, las propuestas canceladas, descartadas, las operaciones rechazadas por
la validación y las que fallaron al ejecutarse. Como la base se recrea en cada ejecución (DC-08),
cada fila guarda el ``trace_id`` de LangSmith, que es la evidencia permanente.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import psycopg
from psycopg.types.json import Jsonb

from app.db.conexion import conectar
from app.ops.modelos import Aviso, PropuestaCambio

Resultado = Literal["ejecutada", "cancelada", "descartada", "rechazada", "fallida"]

_INSERT = """INSERT INTO agente.auditoria
               (empleado_id, operacion, socio_id, resultado, motivo, valores_antes, valores_despues, detalle, trace_id)
             VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"""


def _detalle(avisos: list[Aviso] | None, extra: str | None) -> str | None:
    partes = [json.dumps([a.model_dump() for a in avisos], ensure_ascii=False)] if avisos else []
    if extra:
        partes.append(extra)
    return " | ".join(partes) or None


def insertar(conn: psycopg.Connection, *, empleado_id: int, operacion: str, resultado: Resultado,
             socio_id: int | None = None, motivo: str | None = None, antes: dict[str, Any] | None = None,
             despues: dict[str, Any] | None = None, avisos: list[Aviso] | None = None, detalle: str | None = None,
             trace_id: str | None = None) -> None:
    """Inserta una fila de auditoría usando la conexión (y la transacción) recibida.

    Sin ``RETURNING``: el rol escritor solo tiene ``INSERT`` sobre la auditoría (§4.3).
    """
    conn.execute(_INSERT, (
        empleado_id, operacion[:30], socio_id, resultado, motivo,
        Jsonb(antes) if antes is not None else None, Jsonb(despues) if despues is not None else None,
        _detalle(avisos, detalle), trace_id,
    ))


def registrar(resultado: Resultado, usuario, operacion: str, propuesta: PropuestaCambio | None = None,
              avisos: list[Aviso] | None = None, detalle: str | None = None, trace_id: str | None = None) -> None:
    """Registra una operación no ejecutada (cancelada, descartada, rechazada o fallida) en su propia transacción.

    Nunca lanza excepciones: si la auditoría falla, no debe romper la respuesta al usuario
    (la traza de LangSmith conserva igual el evento).
    """
    try:
        with conectar("escritor") as conn:
            insertar(
                conn, empleado_id=usuario.id, operacion=operacion, resultado=resultado,
                socio_id=propuesta.socio.id if propuesta else None,
                motivo=propuesta.motivo if propuesta else None,
                antes=propuesta.antes if propuesta else None, despues=propuesta.despues if propuesta else None,
                avisos=avisos if avisos is not None else (propuesta.errores if propuesta else None),
                detalle=detalle, trace_id=trace_id,
            )
    except psycopg.Error:
        pass
