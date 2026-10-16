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

    def invocar(self, rol: str, prompt, esquema=None, herramientas=None):
        """Invoca el modelo del ``rol`` (``"rapido"`` o ``"principal"``) con reintentos.

        El otro modelo queda como respaldo ante saturación, límites por minuto o cuota diaria agotada
        (:func:`app.llm.con_reintentos`). Con ``esquema`` usa salida estructurada; con ``herramientas``,
        ``bind_tools`` (el respaldo recibe las mismas herramientas).
        """
        from app.llm import con_reintentos

        def llamada(r: str):
            llm = self.llm_rapido() if r == "rapido" else self.llm_principal()
            if esquema is not None:
                estructurado = llm.with_structured_output(esquema)
            elif herramientas is not None:
                estructurado = llm.bind_tools(herramientas)
            else:
                estructurado = llm

            def invocar(p):
                return estructurado.invoke(p)

            invocar.modelo = getattr(llm, "model", r)
            return invocar

        otro = "principal" if rol == "rapido" else "rapido"
        return con_reintentos(llamada(rol), prompt, respaldo=_Diferido(lambda: llamada(otro)))


class _Diferido:
    """Llamada que construye su función recién al usarse (el respaldo solo se crea si hace falta)."""

    def __init__(self, fabrica) -> None:
        self.fabrica = fabrica
        self._fn = None

    def _resolver(self):
        if self._fn is None:
            self._fn = self.fabrica()
        return self._fn

    @property
    def modelo(self) -> str:
        return self._resolver().modelo

    def __call__(self, *args, **kwargs):
        return self._resolver()(*args, **kwargs)


modelos = _Modelos()
