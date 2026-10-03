"""Nodos del grafo (spec técnico §5.2). Un módulo por grupo de nodos.

Cada nodo recibe el :class:`app.agent.estado.EstadoAgente` y devuelve un diccionario con las
claves que actualiza. Los modelos de lenguaje se obtienen con :func:`modelos`, que los tests
reemplazan por modelos simulados.
"""

from __future__ import annotations

from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def cargar_prompt(nombre: str) -> str:
    """Lee un prompt versionado de ``app/agent/prompts/<nombre>.md``."""
    return (PROMPTS / f"{nombre}.md").read_text(encoding="utf-8")


class _Modelos:
    """Proveedor de los modelos de lenguaje que usan los nodos (inyectable en los tests)."""

    def __init__(self) -> None:
        self.rapido = None
        self.principal = None

    def llm_rapido(self):
        if self.rapido is not None:
            return self.rapido
        from app.llm import llm_rapido

        return llm_rapido()

    def llm_principal(self):
        if self.principal is not None:
            return self.principal
        from app.llm import llm_principal

        return llm_principal()


modelos = _Modelos()
