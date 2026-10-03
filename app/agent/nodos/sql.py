"""Nodos ``generar_sql`` y ``ejecutar_sql`` (spec técnico §5.2 y §6; RF-01 a RF-08, RNF-05).

``generar_sql`` arma una sentencia ``SELECT`` con el catálogo del perfil, la fecha de hoy y, si hubo
un intento anterior, la consulta y el error obtenidos. ``ejecutar_sql`` es la herramienta
``consulta_sql``: valida y ejecuta con el rol del perfil. Si falla, el grafo vuelve a
``generar_sql`` hasta 2 veces más (3 intentos en total).
"""

from __future__ import annotations

import re
from functools import lru_cache

from langsmith import traceable

from app.agent.estado import EstadoAgente
from app.agent.nodos import cargar_prompt, modelos
from app.config import RAIZ
from app.db.lectura import ResultadoLectura, ejecutar_lectura
from app.llm import con_reintentos

MAX_INTENTOS_SQL = 3  # 1 intento + 2 reintentos (RNF-05)

_REGLAS_SOCIO = """- El usuario es un SOCIO: las vistas `mi_*` y `mis_*` ya están filtradas por él, no filtres por su id ni su DNI.
- No hay datos de otros socios ni métricas del negocio. Si la pregunta los pide, consultá solo lo que exista en las
  vistas permitidas (el resultado puede quedar vacío)."""
_REGLAS_ADMIN = "- El usuario es un ADMINISTRADOR: puede leer todas las tablas del catálogo, de las tres sedes."


@lru_cache(maxsize=2)
def catalogo(perfil: str) -> str:
    """Catálogo de esquema y semántica para el perfil (``app/db/catalogos/<perfil>.md``)."""
    return (RAIZ / "app" / "db" / "catalogos" / f"{'admin' if perfil == 'admin' else 'socio'}.md").read_text(encoding="utf-8")


def extraer_sql(texto: str) -> str:
    """Saca la consulta del bloque ```sql (o devuelve el texto tal cual si no hay bloque)."""
    if isinstance(texto, list):  # algunos modelos devuelven partes de contenido
        texto = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in texto)
    m = re.search(r"```(?:sql|postgresql)?\s*(.*?)```", texto, re.S | re.I)
    return (m.group(1) if m else texto).strip().rstrip(";").strip()


def armar_prompt(estado: EstadoAgente) -> str:
    u = estado["usuario"]
    c = estado.get("clasificacion")
    pregunta = (c.pregunta_datos if c and c.pregunta_datos else None) or estado["mensaje"]
    previo = ""
    if estado.get("sql_error"):
        previo = (f"\n## Intento anterior (falló, corregilo)\n```sql\n{estado.get('sql') or ''}\n```\n"
                  f"Error: {estado['sql_error']}\n")
    return (cargar_prompt("generar_sql")
            .replace("<<fecha>>", estado["fecha_hoy"].strftime("%Y-%m-%d (%A)"))
            .replace("<<reglas_perfil>>", _REGLAS_ADMIN if u.es_admin else _REGLAS_SOCIO)
            .replace("<<catalogo>>", catalogo(u.perfil))
            .replace("<<pregunta>>", pregunta)
            .replace("<<intento_anterior>>", previo))


def generar_sql(estado: EstadoAgente) -> dict:
    """Genera la SQL de lectura con el modelo principal."""
    respuesta = con_reintentos(modelos.llm_principal().invoke, armar_prompt(estado))
    return {"sql": extraer_sql(respuesta.content), "nodos": ["generar_sql"]}


@traceable(run_type="tool", name="consulta_sql")
def consulta_sql(sql: str, usuario) -> ResultadoLectura:
    """Herramienta ``consulta_sql``: valida y ejecuta la consulta con el rol del perfil (§6.2, §6.3)."""
    return ejecutar_lectura(sql, usuario)


def ejecutar_sql(estado: EstadoAgente) -> dict:
    """Ejecuta la SQL generada y deja el resultado (o el error) en el estado."""
    r = consulta_sql(estado.get("sql") or "", estado["usuario"])
    return {
        "sql": r.sql,
        "sql_error": r.error,
        "intentos_sql": estado.get("intentos_sql", 0) + 1,
        "filas": r.filas if r.ok else None,
        "columnas": r.columnas if r.ok else None,
        "total_filas": r.total_filas if r.ok else None,
        "herramientas": ["consulta_sql"],
        "nodos": ["ejecutar_sql"],
    }


def ruta_despues_de_ejecutar(estado: EstadoAgente) -> str:
    """Reintenta si falló y quedan intentos; si no, sigue a ``sintetizar``."""
    if estado.get("sql_error") and estado.get("intentos_sql", 0) < MAX_INTENTOS_SQL:
        return "generar_sql"
    return "sintetizar"
