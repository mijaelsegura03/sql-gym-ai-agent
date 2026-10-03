"""Fábrica de modelos de Gemini con reintentos (spec técnico T-02, §1.1).

- :func:`llm_rapido`: modelo rápido (``GEMINI_MODEL_FAST``), usado por el nodo ``clasificar``.
- :func:`llm_principal`: modelo principal (``GEMINI_MODEL_MAIN``), usado por ``generar_sql``,
  ``extraer_operacion`` y ``sintetizar``.
- :func:`llm_juez`: juez de la evaluación (``GEMINI_MODEL_JUEZ``).
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
        self.esperado_total = 0.0  # segundos esperados por el limitador (se descuentan al medir tiempos)
        self.lock = threading.Lock()

    def esperar(self) -> None:
        if self.intervalo <= 0:
            return
        with self.lock:
            espera = self.ultimo + self.intervalo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
                self.esperado_total += espera
            self.ultimo = time.monotonic()


_limite = _LimiteRPM()


def segundos_esperados_por_limite() -> float:
    """Total de segundos que el limitador de RPM hizo esperar en este proceso."""
    return _limite.esperado_total


def limitar_rpm(rpm: int | None) -> None:
    """Espacia las llamadas a Gemini para no superar ``rpm`` requests por minuto (``None`` o 0 lo desactiva)."""
    _limite.intervalo = 60.0 / rpm if rpm else 0.0


class CuotaDiariaAgotada(RuntimeError):
    """Se agotó la cuota diaria del plan gratuito: reintentar no sirve hasta el día siguiente."""


#: Queda en True si alguna llamada de este proceso chocó con la cuota diaria (la evaluación se corta).
cuota_diaria_agotada = False


def es_cuota_diaria(error: BaseException) -> bool:
    """True si el 429 corresponde al límite de requests por día (``...PerDay...``)."""
    texto = str(error).lower()
    return "perday" in texto or "per_day" in texto or "per day" in texto


def es_reintentable(error: BaseException) -> bool:
    """True si el error es un límite de cuota (429) o un error transitorio del servidor."""
    if es_cuota_diaria(error):
        return False
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
            if es_cuota_diaria(e):
                global cuota_diaria_agotada
                cuota_diaria_agotada = True
                raise CuotaDiariaAgotada(
                    "Se agotó la cuota diaria gratuita de Gemini para este modelo; se libera al día siguiente.") from e
            if intento >= maximo or not es_reintentable(e):
                raise
            espera = min(espera_max, espera_inicial * (2 ** intento)) * (0.8 + 0.4 * random.random())
            modelo = getattr(getattr(fn, "__self__", None), "model", "?")
            codigo = getattr(e, "code", None) or getattr(e, "status_code", None) or type(e).__name__
            log.warning("Gemini (%s) devolvió %s; reintento %d en %.1f s — %s", modelo, codigo, intento + 1, espera,
                        " ".join(str(e).split())[:400])
            dormir(espera)
            intento += 1


def _chat(modelo: str, temperature: float, thinking_budget: int | None = None) -> ChatGoogleGenerativeAI:
    s = get_settings()
    extra = {"thinking_budget": thinking_budget} if thinking_budget is not None else {}
    return ChatGoogleGenerativeAI(
        model=modelo,
        temperature=temperature,
        timeout=s.llm_timeout_seg,
        max_retries=0,  # los reintentos los maneja con_reintentos()
        google_api_key=requerir_google_api_key(),
        **extra,
    )


@lru_cache(maxsize=4)
def llm_rapido(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    """Modelo rápido (clasificación). ``temperature=0`` por defecto (§1.1)."""
    s = get_settings()
    return _chat(s.gemini_model_fast, temperature, s.gemini_thinking_fast)


@lru_cache(maxsize=4)
def llm_principal(temperature: float = 0.0) -> ChatGoogleGenerativeAI:
    """Modelo principal (SQL, extracción, síntesis y juez). ``temperature=0`` por defecto."""
    s = get_settings()
    return _chat(s.gemini_model_main, temperature, s.gemini_thinking_main)


@lru_cache(maxsize=1)
def llm_juez() -> ChatGoogleGenerativeAI:
    """Modelo del juez de la evaluación (``GEMINI_MODEL_JUEZ``): usa una cuota distinta del principal."""
    return _chat(get_settings().gemini_model_juez, 0.0)


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
