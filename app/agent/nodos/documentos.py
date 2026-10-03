"""Nodo ``buscar_documentos``: herramienta ``busqueda_documentos`` (spec técnico §5.2, §8; RF-09, RF-12, RF-13).

Busca en Chroma con la consulta en castellano que armó ``clasificar`` (aunque el mensaje esté en
inglés, porque los PDFs están en castellano).
"""

from __future__ import annotations

from langsmith import traceable

from app.agent.estado import EstadoAgente
from app.rag.buscador import Fragmento, buscar


@traceable(run_type="tool", name="busqueda_documentos")
def busqueda_documentos(consulta: str) -> list[Fragmento]:
    """Herramienta ``busqueda_documentos``: fragmentos relevantes con su cita (vacía si nada supera el umbral)."""
    return buscar(consulta)


def buscar_documentos(estado: EstadoAgente) -> dict:
    """Recupera los fragmentos de los documentos para la consulta del mensaje."""
    c = estado.get("clasificacion")
    consulta = (c.consulta_documentos if c and c.consulta_documentos else None) or estado["mensaje"]
    return {"fragmentos": busqueda_documentos(consulta), "herramientas": ["busqueda_documentos"],
            "nodos": ["buscar_documentos"]}
