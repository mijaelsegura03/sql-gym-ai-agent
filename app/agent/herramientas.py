"""Herramientas del agente para ``bind_tools`` (spec técnico §5.7; RF-02, RF-04, RF-09, RF-13, RF-24, RF-25).

El LLM ve solo el nombre, la descripción y los argumentos de cada herramienta. El usuario, el perfil y
la conexión no son argumentos: los pone el nodo ``ejecutar_herramientas`` con el usuario del estado
(RNF-01), así que el modelo no puede pedir datos con otro perfil.

- **Lectura y exploración** (:data:`HERRAMIENTAS_LECTURA`): ``consulta_sql``, ``busqueda_documentos``,
  ``describir_tabla`` y ``valores_columna``. Las recibe el agente en las dos rutas.
- **Escritura** (:data:`HERRAMIENTA_OPERACION`): ``operacion_socio`` **solo propone**. No la ejecuta
  ``ejecutar_herramientas``: la arista del grafo lleva a ``extraer_operacion`` → ``validar_operacion`` →
  ``confirmar`` (DT-10). Solo se le ofrece al LLM en la ruta ``escritura`` de un administrador.
"""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool
from langsmith import traceable
from pydantic import BaseModel, Field

from app.db import esquema
from app.db.esquema import ResultadoExploracion
from app.db.lectura import ResultadoLectura, ejecutar_lectura
from app.rag.buscador import Fragmento, buscar

NOMBRES = ("consulta_sql", "busqueda_documentos", "describir_tabla", "valores_columna")
OPERACION = "operacion_socio"
SIN_RESULTADOS = "SIN RESULTADOS"

_DESCRIPCION_SQL = """Ejecuta UNA consulta SQL de solo lectura (PostgreSQL 16) sobre la base del gimnasio y devuelve \
las columnas, el total de filas y hasta 50 filas en JSON, o `ERROR: <motivo>` si la consulta no pasa la validación o \
falla (en ese caso, corregila y volvé a intentar).
La base tiene los HECHOS operativos: socios, membresías, pagos, aptos médicos, clases y su grilla, sesiones, reservas \
y cupos, accesos al gimnasio, rutinas, empleados, planes y precios vigentes.
Regla de fuente de verdad: precios vigentes, horarios y grilla, cupos, estados, fechas y montos salen de esta base, \
no de los documentos. Usá solo las tablas o vistas del catálogo del prompt; los permisos del usuario se aplican solos."""

_DESCRIPCION_DOCUMENTOS = """Busca en los documentos de políticas del gimnasio y devuelve los fragmentos más \
relevantes, cada uno con su etiqueta [DOC-0X §N, p. P], o `SIN RESULTADOS` si los documentos no cubren el tema.
Los documentos tienen las REGLAS y procedimientos: DOC-01 Reglamento interno y normas de acceso (horarios, condiciones \
de ingreso, motivos de rechazo y cómo se resuelven, cambio de sede, normas de uso, sanciones, invitados, datos \
personales); DOC-02 Política de membresías, pagos y bajas (qué incluye cada plan, medios y estados de pago, cuotas, \
renovación, congelamiento, cambio de plan, bajas y reintegros); DOC-03 Guía de clases grupales y reservas (quién \
reserva, topes semanales, apertura y cancelación de reservas, lista de espera, inasistencias, actividades); DOC-04 \
Manual de salud, apto médico y rutinas (vigencia y requisitos del apto, observaciones y actividades recomendadas, \
rutinas, seguridad, emergencias).
Regla de fuente de verdad: reglas, procedimientos y explicaciones salen de los documentos; los precios y horarios \
vigentes, de la base. Escribí la consulta SIEMPRE en castellano (los documentos están en castellano), con las \
palabras clave del tema, aunque el mensaje del usuario esté en inglés."""

_DESCRIPCION_DESCRIBIR = """Describe una tabla (o vista) de la base: sus columnas con el tipo y si admiten NULL, sus \
claves foráneas y cuántas filas visibles tiene. Usala SOLO si dudás de un nombre de columna o de una relación que el \
catálogo del prompt no aclara; si el catálogo ya lo dice, escribí directamente la consulta. Devuelve `ERROR: <motivo>` \
con las tablas disponibles si el nombre no existe para este perfil."""

_DESCRIPCION_VALORES = """Lista los valores distintos que toma una columna (hasta 30, los más frecuentes, con su \
cantidad de filas). Usala SOLO si necesitás el valor exacto de un campo para filtrar (por ejemplo, cómo está escrito \
un estado, un método de pago o un nombre de actividad) y el catálogo del prompt no lo dice. Devuelve `ERROR: <motivo>` \
si la tabla o la columna no existen para este perfil."""

_DESCRIPCION_OPERACION = """Propone la operación de escritura que pidió el administrador (alta, modificación de \
datos, suspensión, reactivación o baja de UN socio, o cualquier otro pedido de cambio). NO ejecuta nada: el sistema \
interpreta el pedido original, lo valida con las reglas del catálogo y le muestra la propuesta al administrador para \
que la confirme o la cancele. Llamala UNA sola vez, sola (sin otras herramientas en el mismo paso), cuando ya tengas \
lo necesario; después no vas a recibir más resultados."""


class ArgsConsultaSQL(BaseModel):
    """Argumentos de ``consulta_sql``: solo la consulta."""

    sql: str = Field(description="Una sola sentencia SELECT (se permite WITH … SELECT) de PostgreSQL 16")


class ArgsBusquedaDocumentos(BaseModel):
    """Argumentos de ``busqueda_documentos``: solo el texto a buscar."""

    consulta: str = Field(description="Texto de búsqueda en castellano, con las palabras clave del tema")


class ArgsDescribirTabla(BaseModel):
    """Argumentos de ``describir_tabla``: solo el nombre."""

    tabla: str = Field(description="Nombre de la tabla o vista del catálogo, por ejemplo 'membresia' o 'mis_pagos'")


class ArgsValoresColumna(BaseModel):
    """Argumentos de ``valores_columna``: tabla y columna."""

    tabla: str = Field(description="Nombre de la tabla o vista del catálogo")
    columna: str = Field(description="Nombre de la columna")


class ArgsOperacionSocio(BaseModel):
    """Argumentos de ``operacion_socio``: solo aclaraciones; los datos de la operación salen del mensaje (DT-10)."""

    aclaraciones: str = Field(
        "", description="Solo lo que encontraste en la base y el mensaje no dice de forma directa, por ejemplo "
                        "'El socio con email x@example.com es el DNI 24498545'. Vacío si el mensaje ya identifica "
                        "todo. No repitas, completes ni cambies los datos que dio el administrador.")


def _solo_en_el_grafo(**_kwargs) -> str:
    raise RuntimeError("Las herramientas del agente las ejecuta el grafo con el usuario del estado.")


def _herramienta(nombre: str, descripcion: str, args) -> StructuredTool:
    return StructuredTool.from_function(func=_solo_en_el_grafo, name=nombre, description=descripcion, args_schema=args)


HERRAMIENTAS_LECTURA = [
    _herramienta("consulta_sql", _DESCRIPCION_SQL, ArgsConsultaSQL),
    _herramienta("busqueda_documentos", _DESCRIPCION_DOCUMENTOS, ArgsBusquedaDocumentos),
    _herramienta("describir_tabla", _DESCRIPCION_DESCRIBIR, ArgsDescribirTabla),
    _herramienta("valores_columna", _DESCRIPCION_VALORES, ArgsValoresColumna),
]
HERRAMIENTA_OPERACION = _herramienta(OPERACION, _DESCRIPCION_OPERACION, ArgsOperacionSocio)


def herramientas_para(ruta: str, usuario) -> list[StructuredTool]:
    """Herramientas que recibe el LLM: ``operacion_socio`` solo en la ruta ``escritura`` de un administrador (RF-25)."""
    if ruta == "escritura" and usuario.es_admin:
        return [*HERRAMIENTAS_LECTURA, HERRAMIENTA_OPERACION]
    return list(HERRAMIENTAS_LECTURA)


# ---------------------------------------------------------------------------
# Ejecución (la llama ejecutar_herramientas con el usuario del estado)
# ---------------------------------------------------------------------------
@traceable(run_type="tool", name="consulta_sql")
def consulta_sql(sql: str, usuario) -> ResultadoLectura:
    """Valida y ejecuta la consulta con el rol del perfil del usuario (§6.2, §6.3)."""
    return ejecutar_lectura(sql, usuario)


@traceable(run_type="tool", name="busqueda_documentos")
def busqueda_documentos(consulta: str) -> list[Fragmento]:
    """Fragmentos relevantes con su cita (vacía si nada supera el umbral, §8.2)."""
    return buscar(consulta)


@traceable(run_type="tool", name="describir_tabla")
def describir_tabla(tabla: str, usuario) -> ResultadoExploracion:
    """Estructura de una tabla o vista permitida para el perfil (§6.4)."""
    return esquema.describir_tabla(tabla, usuario)


@traceable(run_type="tool", name="valores_columna")
def valores_columna(tabla: str, columna: str, usuario) -> ResultadoExploracion:
    """Valores distintos de una columna, con los filtros del perfil (§6.4)."""
    return esquema.valores_columna(tabla, columna, usuario)


def texto_resultado_exploracion(r: ResultadoExploracion) -> str:
    """Resultado de ``describir_tabla`` o ``valores_columna`` para el ``ToolMessage``, o ``ERROR: …``."""
    return r.texto if r.ok else f"ERROR: {r.error}"


def resumen_exploracion(r: ResultadoExploracion) -> str:
    """Resumen corto para el detalle: ``"11 columnas"``, ``"5 valores"`` o ``"error: …"``."""
    return r.resumen if r.ok else f"error: {r.error}"


def texto_resultado_sql(r: ResultadoLectura) -> str:
    """Resultado de ``consulta_sql`` para el ``ToolMessage``: columnas, total y filas, o ``ERROR: …``."""
    if not r.ok:
        return f"ERROR: {r.error}"
    lineas = [f"Columnas: {', '.join(r.columnas)}",
              f"Total de filas: {r.total_filas}" + (f" (se muestran las primeras {len(r.filas)})"
                                                    if r.total_filas > len(r.filas) else "")]
    lineas += [json.dumps(f, ensure_ascii=False, default=str) for f in r.filas]
    return "\n".join(lineas)


def texto_resultado_busqueda(fragmentos: list[Fragmento]) -> str:
    """Resultado de ``busqueda_documentos`` para el ``ToolMessage``: fragmentos con su etiqueta, o ``SIN RESULTADOS``."""
    if not fragmentos:
        return SIN_RESULTADOS
    return "\n\n".join(f"{f.etiqueta}\n{f.texto.split(chr(10), 1)[-1]}" for f in fragmentos)


def resumen_sql(r: ResultadoLectura) -> str:
    """Resumen corto para el detalle: ``"12 filas"`` o ``"error: …"``."""
    if not r.ok:
        return f"error: {r.error}"
    return f"{r.total_filas} fila{'s' if r.total_filas != 1 else ''}"


def resumen_busqueda(fragmentos: list[Fragmento]) -> str:
    """Resumen corto para el detalle: ``"4 fragmentos (DOC-01, DOC-04)"`` o ``"sin resultados"``."""
    if not fragmentos:
        return "sin resultados"
    docs = ", ".join(dict.fromkeys(f.doc_id for f in fragmentos))
    return f"{len(fragmentos)} fragmento{'s' if len(fragmentos) != 1 else ''} ({docs})"
