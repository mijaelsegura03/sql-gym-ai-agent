"""Estado del grafo, salida de la clasificación y respuesta hacia la interfaz (spec técnico §5.1, §5.4, §5.6)."""

from __future__ import annotations

import operator
from datetime import date
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.auth import Usuario
from app.ops.modelos import PropuestaCambio, ResultadoEjecucion, ResultadoValidacion
from app.rag.buscador import Fragmento

Ruta = Literal["directa", "fuera_dominio", "necesita_contexto", "lectura", "escritura"]


class Clasificacion(BaseModel):
    """Salida estructurada del nodo ``clasificar`` (§5.4)."""

    ruta: Ruta = Field(description="Cómo tratar el mensaje")
    idioma: Literal["es", "en"] = Field(description="Idioma del mensaje: es (castellano) o en (inglés)")
    pide_info_interna: bool = Field(
        description="True si pide datos de otros socios, de empleados (salvo nombre del instructor) o métricas del negocio")
    respuesta_directa: str | None = Field(
        None, description="Solo para directa, fuera_dominio y necesita_contexto: la respuesta al usuario, en su idioma")


class ConsultaEjecutada(BaseModel):
    """Una invocación de ``consulta_sql`` del agente de lectura (§5.1)."""

    sql: str | None                       # None en la respuesta al socio (RF-56)
    ok: bool
    error: str | None = None
    columnas: list[str] | None = None
    filas: list[dict[str, Any]] | None = None   # hasta 50 (RF-04)
    total_filas: int | None = None


class PasoAgente(BaseModel):
    """Una invocación de herramienta del agente, para el detalle de la respuesta (§5.1)."""

    herramienta: Literal["consulta_sql", "busqueda_documentos", "describir_tabla", "valores_columna", "operacion_socio"]
    argumentos: dict[str, Any] = {}       # los del LLM; sin la SQL para el socio (RF-56)
    resumen: str                          # "12 filas", "error: ...", "4 fragmentos (DOC-01, DOC-04)", "propuesta lista…"


class EstadoAgente(TypedDict, total=False):
    """Estado del grafo LangGraph. El DNI del usuario nunca forma parte del estado."""

    usuario: Usuario
    mensaje: str
    fecha_hoy: date
    trace_id: str | None
    clasificacion: Clasificacion | None
    # Agente (§5.7)
    mensajes_agente: Annotated[list[AnyMessage], add_messages]   # conversación interna, solo de este mensaje
    pasos_agente: int                                            # rondas de herramientas (MAX_PASOS_AGENTE)
    consultas: Annotated[list[ConsultaEjecutada], operator.add]  # cada invocación de consulta_sql
    pasos: Annotated[list[PasoAgente], operator.add]             # cada invocación de herramienta
    sql: str | None                       # de la última consulta exitosa (o de la última, si ninguna lo fue)
    sql_error: str | None                 # solo si ninguna consulta terminó bien
    filas: list[dict[str, Any]] | None    # ídem: última consulta exitosa
    columnas: list[str] | None
    total_filas: int | None
    fragmentos: list[Fragmento]           # todos los recuperados, sin repetir
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
    citas: list[str]                                  # "DOC-0X §N" citados (RF-10)
    ruta_final: str | None


class RespuestaAgente(BaseModel):
    """Lo que recibe la interfaz por cada mensaje (§5.6)."""

    texto: str
    ruta: str
    idioma: str = "es"
    herramientas: list[str] = []
    fuentes: list[str] = []            # "base de datos" y/o el título de cada documento citado (RF-19)
    citas: list[str] = []              # "DOC-0X §N" citados, para el detalle (RF-10)
    sql: str | None = None             # None si el perfil es socio (RF-56): se filtra en código
    columnas: list[str] | None = None
    filas: list[dict[str, Any]] | None = None   # hasta 50 (RF-04)
    total_filas: int | None = None
    fragmentos: list[Fragmento] = []
    consultas: list[ConsultaEjecutada] = []     # todas las del agente; sql y filas son las de la última exitosa
    pasos: list[PasoAgente] = []                # herramientas invocadas en orden, con sus argumentos (RF-24)
    propuesta: PropuestaCambio | None = None
    propuesta_pendiente: bool = False  # True si el grafo quedó esperando Confirmar/Cancelar
    resultado_operacion: ResultadoEjecucion | None = None
    nodos: list[str] = []
    thread_id: str
    trace_id: str | None = None
    duracion_seg: float | None = None
    error: bool = False
