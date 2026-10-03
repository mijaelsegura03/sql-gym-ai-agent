"""Búsqueda semántica sobre los PDFs indexados (spec técnico §8.2 a §8.4).

``buscar(consulta)`` embebe la consulta (en castellano, la arma ``clasificar``) con
``RETRIEVAL_QUERY``, busca los ``k`` fragmentos más cercanos por similitud coseno en la colección
``politicas`` y descarta los que superan ``RAG_DISTANCIA_MAX``. Si no queda ninguno, devuelve una
lista vacía y ``sintetizar`` responde que los documentos no cubren el tema (RF-11).
"""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import BaseModel

from app.config import get_settings
from app.rag.ingesta import COLECCION, cliente_chroma


class Fragmento(BaseModel):
    """Fragmento recuperado de un documento, con su cita."""

    doc_id: str
    seccion: str
    pagina: int
    texto: str
    distancia: float | None = None

    @property
    def numero_seccion(self) -> str:
        """``"4. Motivos…"`` → ``"§4"``; ``"Resumen"`` → ``"§Resumen"``."""
        m = re.match(r"^(\d+)\.", self.seccion)
        return f"§{m.group(1)}" if m else f"§{self.seccion.split(' —')[0]}"

    @property
    def etiqueta(self) -> str:
        """Etiqueta de cita que usa ``sintetizar``: ``[DOC-01 §4, p. 2]``."""
        return f"[{self.doc_id} {self.numero_seccion}, p. {self.pagina}]"

    @property
    def fuente(self) -> str:
        """Fuente para mostrar en la respuesta: ``DOC-01 §4``."""
        return f"{self.doc_id} {self.numero_seccion}"


@lru_cache(maxsize=1)
def _coleccion():
    return cliente_chroma().get_collection(COLECCION)


def buscar(consulta: str, k: int | None = None, distancia_max: float | None = None) -> list[Fragmento]:
    """Devuelve los fragmentos más relevantes para ``consulta``, ordenados por cercanía.

    Args:
        consulta: texto de búsqueda (en castellano, §8.3).
        k: cantidad máxima de fragmentos (por defecto ``RAG_TOP_K``).
        distancia_max: distancia coseno máxima aceptada (por defecto ``RAG_DISTANCIA_MAX``).
    """
    from app.llm import embeber_consulta

    s = get_settings()
    k = k or s.rag_top_k
    distancia_max = s.rag_distancia_max if distancia_max is None else distancia_max
    res = _coleccion().query(query_embeddings=[embeber_consulta(consulta)], n_results=k,
                             include=["documents", "metadatas", "distances"])
    fragmentos = []
    for texto, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
        if dist > distancia_max:
            continue
        fragmentos.append(Fragmento(doc_id=meta["doc_id"], seccion=meta["seccion"], pagina=int(meta["pagina"]),
                                    texto=texto, distancia=round(float(dist), 4)))
    return fragmentos
