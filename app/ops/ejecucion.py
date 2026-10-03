"""Ejecución atómica de una propuesta confirmada (spec técnico §7.3; RF-48, RNF-02, RNF-03).

1. Abre una transacción con ``gym_escritor``.
2. Bloquea la fila del socio con ``SELECT … FOR UPDATE``.
3. Revalida con las mismas reglas del §7.2. Si algo cambió desde la propuesta, hace rollback y
   registra la auditoría como ``fallida``.
4. Aplica los cambios y los efectos secundarios con sentencias parametrizadas **fijas**, escritas en
   este módulo: nunca se ejecuta SQL generada por el LLM.
5. Inserta la auditoría ``ejecutada`` con los valores de antes y después y el ``trace_id``.
6. Commit: se aplica todo o nada.
"""

from __future__ import annotations

from datetime import date
from typing import Callable

import psycopg
from psycopg import sql

from app.db.conexion import conectar
from app.ops import auditoria
from app.ops.modelos import Aviso, PropuestaCambio, ResultadoEjecucion
from app.ops.validacion import validar

# Columnas de socio que puede escribir una modificación (lista cerrada; el DNI no está)
_COLUMNAS_MODIFICABLES = {"nombre", "apellido", "email", "telefono", "fecha_nacimiento", "contacto_emergencia",
                          "sede_principal_id"}

_INSERT_SOCIO = """INSERT INTO public.socio (dni, nombre, apellido, email, telefono, fecha_nacimiento,
                                             contacto_emergencia, sede_principal_id, fecha_alta, estado)
                   VALUES (%(dni)s, %(nombre)s, %(apellido)s, %(email)s, %(telefono)s, %(fecha_nacimiento)s,
                           %(contacto_emergencia)s, %(sede_principal_id)s, %(fecha_alta)s, 'activo')
                   RETURNING id"""
_UPDATE_ESTADO = "UPDATE public.socio SET estado = %s WHERE id = %s"
_CANCELAR_MEMBRESIAS = "UPDATE public.membresia SET estado = 'cancelada' WHERE id = ANY(%s) AND socio_id = %s"


class _Revalidacion(Exception):
    def __init__(self, errores: list[Aviso]) -> None:
        self.errores = errores


def _comparable(p: PropuestaCambio) -> tuple:
    return (p.operacion, p.socio.id, p.antes, p.despues, sorted(p.membresias_a_cancelar), p.datos)


def ejecutar(propuesta: PropuestaCambio, operacion, usuario, hoy: date | None = None, trace_id: str | None = None,
             _falla_simulada: Callable[[psycopg.Connection], None] | None = None) -> ResultadoEjecucion:
    """Ejecuta una propuesta confirmada por el administrador.

    Args:
        propuesta: la propuesta que vio y confirmó el administrador.
        operacion: la operación extraída que originó la propuesta (se revalida con ella).
        usuario: administrador de la sesión.
        hoy: fecha real (inyectable en los tests).
        trace_id: id de la traza de LangSmith, para la auditoría.
        _falla_simulada: solo para tests; se llama después de aplicar los cambios y antes del
            commit, para verificar la atomicidad.
    """
    hoy = hoy or date.today()
    try:
        with conectar("escritor") as conn:
            with conn.transaction():
                # 2 y 3: bloqueo de la fila del socio y revalidación con los datos actuales
                actual = validar(operacion, usuario, hoy, conn=conn, bloquear_socio=True)
                if not actual.valida:
                    raise _Revalidacion(actual.errores)
                if _comparable(actual.propuesta) != _comparable(propuesta):
                    raise _Revalidacion([Aviso(codigo="estado_cambio")])
                p = actual.propuesta

                # 4: cambios con sentencias fijas
                socio_id = p.socio.id
                if p.operacion == "alta_socio":
                    socio_id = conn.execute(_INSERT_SOCIO, p.datos).fetchone()["id"]
                elif p.operacion == "modificacion_socio":
                    columnas = [c for c in p.datos if c in _COLUMNAS_MODIFICABLES]
                    sentencia = sql.SQL("UPDATE public.socio SET {} WHERE id = %s").format(
                        sql.SQL(", ").join(sql.SQL("{} = %s").format(sql.Identifier(c)) for c in columnas))
                    conn.execute(sentencia, [p.datos[c] for c in columnas] + [socio_id])
                else:  # suspension | reactivacion | baja
                    conn.execute(_UPDATE_ESTADO, (p.datos["estado"], socio_id))
                    if p.membresias_a_cancelar:
                        conn.execute(_CANCELAR_MEMBRESIAS, (p.membresias_a_cancelar, socio_id))

                if _falla_simulada:
                    _falla_simulada(conn)

                # 5: auditoría en la misma transacción
                despues = dict(p.despues)
                if p.membresias_a_cancelar:
                    despues["membresias_canceladas"] = p.membresias_a_cancelar
                auditoria.insertar(conn, empleado_id=usuario.id, operacion=p.operacion, resultado="ejecutada",
                                   socio_id=socio_id, motivo=p.motivo, antes=p.antes, despues=despues,
                                   avisos=p.advertencias, trace_id=trace_id)
            # 6: commit al salir del bloque de transacción
            return ResultadoEjecucion(ok=True, operacion=p.operacion, socio_id=socio_id)
    except _Revalidacion as e:
        auditoria.registrar("fallida", usuario, propuesta.operacion, propuesta, avisos=e.errores,
                            detalle="revalidación al confirmar", trace_id=trace_id)
        return ResultadoEjecucion(ok=False, operacion=propuesta.operacion, socio_id=propuesta.socio.id, errores=e.errores)
    except Exception as e:  # noqa: BLE001 - incluye errores de la base y fallas simuladas
        auditoria.registrar("fallida", usuario, propuesta.operacion, propuesta,
                            avisos=[Aviso(codigo="error_ejecucion")], detalle=f"{type(e).__name__}: {e}"[:500],
                            trace_id=trace_id)
        return ResultadoEjecucion(ok=False, operacion=propuesta.operacion, socio_id=propuesta.socio.id,
                                  errores=[Aviso(codigo="error_ejecucion")])
