"""Estado del grafo, salida de la clasificación y respuesta hacia la interfaz (spec técnico §5.1, §5.4, §5.6)."""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from pydantic import BaseModel, Field

from app.auth import Usuario
from app.ops.modelos import PropuestaCambio, ResultadoEjecucion, ResultadoValidacion
from app.rag.buscador import Fragmento

Ruta = Literal["directa", "fuera_dominio", "necesita_contexto", "datos", "documentos", "hibrida", "escritura"]


class Clasificacion(BaseModel):
    """Salida estructurada del nodo ``clasificar`` (§5.4)."""

    ruta: Ruta = Field(description="Cómo tratar el mensaje")
    idioma: Literal["es", "en"] = Field(description="Idioma del mensaje: es (castellano) o en (inglés)")
    pide_info_interna: bool = Field(
        description="True si pide datos de otros socios, de empleados (salvo nombre del instructor) o métricas del negocio")
    pregunta_datos: str | None = Field(None, description="Parte del mensaje que se responde con la base de datos")
    consulta_documentos: str | None = Field(
        None, description="Consulta de búsqueda en los documentos, reformulada SIEMPRE en castellano")
    respuesta_directa: str | None = Field(
        None, description="Solo para directa, fuera_dominio y necesita_contexto: la respuesta al usuario, en su idioma")


class EstadoAgente(TypedDict, total=False):
    """Estado del grafo LangGraph. El DNI del usuario nunca forma parte del estado (RF-71)."""

    usuario: Usuario
    mensaje: str
    fecha_hoy: date
    trace_id: str | None
    clasificacion: Clasificacion | None
    # Rama de datos
    sql: str | None
    sql_error: str | None
    intentos_sql: int
    filas: list[dict[str, Any]] | None
    columnas: list[str] | None
    total_filas: int | None
    # Rama de documentos
    fragmentos: list[Fragmento]
    # Rama de escritura
    operacion: Any | None                 # OperacionSocio
    validacion: ResultadoValidacion | None
    propuesta: PropuestaCambio | None
    decision: Literal["confirmar", "cancelar"] | None
    ejecucion: ResultadoEjecucion | None
    # Salida
    herramientas: Annotated[list[str], operator.add]  # "consulta_sql", "busqueda_documentos", "operacion_socio"
    nodos: Annotated[list[str], operator.add]         # recorrido, para el detalle y los tests
    respuesta: str | None
    fuentes: list[str]
    ruta_final: str | None


class RespuestaAgente(BaseModel):
    """Lo que recibe la interfaz por cada mensaje (§5.6)."""

    texto: str
    ruta: str
    idioma: str = "es"
    herramientas: list[str] = []
    fuentes: list[str] = []
    sql: str | None = None             # None si el perfil es socio (RF-56): se filtra en código
    columnas: list[str] | None = None
    filas: list[dict[str, Any]] | None = None   # hasta 50 (RF-04)
    total_filas: int | None = None
    fragmentos: list[Fragmento] = []
    propuesta: PropuestaCambio | None = None
    propuesta_pendiente: bool = False  # True si el grafo quedó esperando Confirmar/Cancelar
    resultado_operacion: ResultadoEjecucion | None = None
    nodos: list[str] = []
    thread_id: str
    trace_id: str | None = None
    duracion_seg: float | None = None
    error: bool = False
