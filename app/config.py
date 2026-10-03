"""Configuración de la aplicación, leída del archivo ``.env`` y de variables de entorno.

Se usa ``pydantic-settings``: cada campo corresponde a una variable de ``.env.example``
(spec técnico §10.3). Las variables de entorno del proceso tienen prioridad sobre el
archivo, lo que permite que ``start.py`` cambie, por ejemplo, ``DB_PORT`` para una
ejecución puntual.

Uso::

    from app.config import get_settings
    s = get_settings()
    s.db_port
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Raíz del repositorio (carpeta que contiene ``app/``).
RAIZ = Path(__file__).resolve().parent.parent


class ConfiguracionError(RuntimeError):
    """Falta una variable obligatoria o tiene un valor inválido."""


class Settings(BaseSettings):
    """Variables de configuración. Los nombres coinciden con los de ``.env`` (sin distinguir mayúsculas)."""

    model_config = SettingsConfigDict(
        env_file=RAIZ / ".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # LLM (T-02, §1.1)
    google_api_key: str = ""
    gemini_model_fast: str = "gemini-3.5-flash-lite"
    gemini_model_main: str = "gemini-3.8-flash"
    gemini_embedding_model: str = "gemini-embedding-001"
    llm_max_reintentos: int = 6
    llm_timeout_seg: float = 60.0

    # Base de datos (§4)
    postgres_user: str = "postgres"
    postgres_password: str = Field(min_length=1)
    db_host: str = "localhost"
    db_port: int = 5433
    db_name: str = "gimnasio"
    db_pass_lector_admin: str = Field(min_length=1)
    db_pass_lector_socio: str = Field(min_length=1)
    db_pass_escritor: str = Field(min_length=1)

    # RAG (§8)
    chroma_dir: str = ".chroma"
    rag_top_k: int = 5
    rag_distancia_max: float = 0.6

    # Observabilidad (§9)
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "gimnasio-agente"

    # Varios
    pseudonimo_secret: str = Field(min_length=1)
    eval_rpm: int = 10

    @property
    def chroma_path(self) -> Path:
        """Ruta absoluta de la carpeta de Chroma."""
        p = Path(self.chroma_dir)
        return p if p.is_absolute() else RAIZ / p

    @property
    def trazas_activas(self) -> bool:
        """True si las trazas de LangSmith están habilitadas y hay API key."""
        return self.langsmith_tracing and bool(self.langsmith_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración (cacheada).

    Raises:
        ConfiguracionError: si falta una variable obligatoria; el mensaje dice cuál.
    """
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as e:
        faltantes = [str(err["loc"][0]).upper() for err in e.errors()]
        raise ConfiguracionError(
            "Faltan o son inválidas estas variables de configuración: "
            + ", ".join(faltantes)
            + ". Revisá el archivo .env (o ejecutá `python start.py`, que las genera)."
        ) from None


def configurar_trazas() -> bool:
    """Exporta la configuración de LangSmith a las variables de entorno que leen LangChain y LangGraph.

    Si no hay ``LANGSMITH_API_KEY``, desactiva las trazas (la app funciona igual). Devuelve True si
    las trazas quedan activas (RF-70).
    """
    import os

    s = get_settings()
    if s.trazas_activas:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_API_KEY"] = s.langsmith_api_key
        os.environ["LANGSMITH_PROJECT"] = s.langsmith_project
        return True
    os.environ["LANGSMITH_TRACING"] = "false"
    return False


def requerir_google_api_key() -> str:
    """Devuelve ``GOOGLE_API_KEY`` o falla con un mensaje claro si no está configurada."""
    key = get_settings().google_api_key
    if not key:
        raise ConfiguracionError("Falta la variable GOOGLE_API_KEY en el archivo .env.")
    return key
