"""Fábrica de modelos de Gemini con reintentos (spec técnico T-02, §1.1).

- :func:`llm_rapido`: modelo rápido (``GEMINI_MODEL_FAST``), usado por el nodo ``clasificar``.
- :func:`llm_principal`: modelo principal (``GEMINI_MODEL_MAIN``), usado por ``generar_sql``,
  ``extraer_operacion``, ``sintetizar`` y el juez de la evaluación.
- :func:`embeddings`: modelo de embeddings (``GEMINI_EMBEDDING_MODEL``).

Los nombres de los modelos salen siempre de la configuración. El plan gratuito de Gemini tiene
límites de requests por minuto, así que toda llamada pasa por :func:`con_reintentos`, que
reintenta ante el error 429 (``RESOURCE_EXHAUSTED``) y los errores transitorios del servidor
con espera exponencial y un máximo configurable (``LLM_MAX_REINTENTOS``).
"""

from __future__ import annotations

import logging
import random
import threading
import time
from functools import lru_cache
from typing import Any, Callable, TypeVar

from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings

from app.config import get_settings, requerir_google_api_key

log = logging.getLogger(__name__)
T = TypeVar("T")

_MARCAS_REINTENTABLES = ("429", "resource_exhausted", "resource exhausted", "rate limit", "quota",
                         "500", "502", "503", "504", "unavailable", "overloaded", "deadline", "timed out")


class _LimiteRPM:
    """Limitador opcional de requests por minuto a Gemini (lo activa la evaluación con ``EVAL_RPM``)."""

    def __init__(self) -> None:
        self.intervalo = 0.0
        self.ultimo = 0.0
        self.lock = threading.Lock()

    def esperar(self) -> None:
        if self.intervalo <= 0:
            return
        with self.lock:
            espera = self.ultimo + self.intervalo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            self.ultimo = time.monotonic()


_limite = _LimiteRPM()


def limitar_rpm(rpm: int | None) -> None:
    """Espacia las llamadas a Gemini para no superar ``rpm`` requests por minuto (``None`` o 0 lo desactiva)."""
    _limite.intervalo = 60.0 / rpm if rpm else 0.0


def es_reintentable(error: BaseException) -> bool:
    """True si el error es un límite de cuota (429) o un error transitorio del servidor."""
    codigo = getattr(error, "code", None) or getattr(error, "status_code", None)
    if codigo in (429, 500, 502, 503, 504):
        return True
    texto = f"{type(error).__name__} {error}".lower()
    return any(m in texto for m in _MARCAS_REINTENTABLES)


def con_reintentos(fn: Callable[..., T], *args: Any, max_reintentos: int | None = None,
                   espera_inicial: float = 2.0, espera_max: float = 60.0,
                   dormir: Callable[[float], None] = time.sleep, **kwargs: Any) -> T:
    """Llama a ``fn(*args, **kwargs)`` y reintenta con *backoff* exponencial si el error es reintentable.

    Args:
        fn: función a invocar (por ejemplo ``modelo.invoke``).
        max_reintentos: máximo de reintentos (por defecto ``LLM_MAX_REINTENTOS``).
        espera_inicial: espera antes del primer reintento, en segundos; se duplica en cada intento.
        espera_max: tope de la espera entre intentos.
        dormir: función de espera (inyectable en los tests).

    Raises:
        La última excepción, si se agotan los reintentos o el error no es reintentable.
    """
    maximo = get_settings().llm_max_reintentos if max_reintentos is None else max_reintentos
    intento = 0
    while True:
        try:
            _limite.esperar()
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            if intento >= maximo or not es_reintentable(e):
                raise
            espera = min(espera_max, espera_inicial * (2 ** intento)) * (0.8 + 0.4 * random.random())
            log.warning("Gemini devolvió un error reintentable (%s); reintento %d en %.1f s",
                        type(e).__name__, intento + 1, espera)
            dormir(espera)
            intento += 1


def _chat(modelo: str, temperature: float) -> ChatGoogleGenerativeAI:
    s = get_settings()
    return ChatGoogleGenerativeAI(
        model=modelo,
        temperature=temperature,
        timeout=s.llm_timeout_seg,
        max_retries=0,  # los reintentos los maneja con_reintentos()
        google_api_key=requerir_google_api_key(),
    )


@lru_cache(maxsize=4)
def llm_rapido(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    """Modelo rápido (clasificación). ``temperature=0`` por defecto (§1.1)."""
    return _chat(get_settings().gemini_model_fast, temperature)


@lru_cache(maxsize=4)
def llm_principal(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    """Modelo principal (SQL, extracción, síntesis y juez). ``temperature=0`` por defecto."""
    return _chat(get_settings().gemini_model_main, temperature)


@lru_cache(maxsize=1)
def embeddings() -> GoogleGenerativeAIEmbeddings:
    """Modelo de embeddings. El ``task_type`` se indica en cada llamada (documento o consulta)."""
    return GoogleGenerativeAIEmbeddings(
        model=get_settings().gemini_embedding_model,
        google_api_key=requerir_google_api_key(),
    )


def embeber_documentos(textos: list[str]) -> list[list[float]]:
    """Embeddings de fragmentos para indexar (``RETRIEVAL_DOCUMENT``), con reintentos."""
    return con_reintentos(embeddings().embed_documents, textos, task_type="RETRIEVAL_DOCUMENT")


def embeber_consulta(texto: str) -> list[float]:
    """Embedding de una consulta de búsqueda (``RETRIEVAL_QUERY``), con reintentos."""
    return con_reintentos(embeddings().embed_query, texto, task_type="RETRIEVAL_QUERY")
