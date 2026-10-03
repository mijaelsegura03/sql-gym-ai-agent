"""Nodo ``sintetizar`` (spec técnico §5.2, §8.4; RF-05, RF-07, RF-10, RF-11, RF-18, RF-19, RF-22, RF-23).

Redacta la respuesta con las filas de la base y/o los fragmentos de los documentos, en el idioma del
mensaje. Las **fuentes** se arman en código: "base de datos" si la consulta se ejecutó, y las
etiquetas ``[DOC-0X §N, p. P]`` que el texto realmente cita y que corresponden a fragmentos
recuperados (nunca texto libre del LLM).

Si la consulta a la base falló después de los reintentos y no hay fragmentos, la respuesta es un
mensaje fijo y comprensible, sin trazas técnicas y sin llamar al LLM (RF-23).
"""

from __future__ import annotations

import json
import re

from app.agent.estado import EstadoAgente
from app.agent.nodos import cargar_prompt, modelos
from app.llm import con_reintentos
from app.rag.buscador import Fragmento

_ERROR_DATOS = {
    "es": "No pude obtener esa información de la base de datos en este momento. Probá reformular la pregunta "
          "o volvé a intentar en unos minutos.",
    "en": "I couldn't get that information from the database right now. Try rephrasing the question or try "
          "again in a few minutes.",
}
_ETIQUETA_FUENTE = {"es": "Fuente", "en": "Source"}
_BASE = {"es": "base de datos", "en": "database"}
_RE_CORCHETE = re.compile(r"\[([^\]]*DOC-\d{2}[^\]]*)\]")
_RE_CITA = re.compile(r"(DOC-\d{2})\s+(§[\wÁÉÍÓÚáéíóúñ]+)")


def _texto(contenido) -> str:
    if isinstance(contenido, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in contenido)
    return str(contenido or "")


def _bloque_datos(estado: EstadoAgente) -> str:
    if "consulta_sql" not in estado.get("herramientas", []):
        return "(no se consultó la base de datos)"
    if estado.get("sql_error"):
        return "(la consulta a la base falló; no hay datos disponibles)"
    filas = estado.get("filas") or []
    total = estado.get("total_filas") or 0
    if not filas:
        return "(la consulta no devolvió ninguna fila)"
    encabezado = f"Filas: {len(filas)}" + (f" (se muestran 50 de {total} en total)" if total > len(filas) else "")
    return encabezado + "\n" + "\n".join(json.dumps(f, ensure_ascii=False, default=str) for f in filas)


def _bloque_fragmentos(estado: EstadoAgente) -> str:
    if "busqueda_documentos" not in estado.get("herramientas", []):
        return "(no se consultaron los documentos)"
    fragmentos: list[Fragmento] = estado.get("fragmentos") or []
    if not fragmentos:
        return "(no se encontraron fragmentos relevantes: los documentos no cubren este tema)"
    return "\n\n".join(f"{f.etiqueta}\n{f.texto.split(chr(10), 1)[-1]}" for f in fragmentos)


def armar_prompt(estado: EstadoAgente) -> str:
    u = estado["usuario"]
    idioma = estado["clasificacion"].idioma
    return (cargar_prompt("sintetizar")
            .replace("<<perfil>>", "Administrador" if u.es_admin else "Socio")
            .replace("<<nombre>>", u.nombre)
            .replace("<<fecha>>", estado["fecha_hoy"].strftime("%d/%m/%Y (%A)"))
            .replace("<<idioma>>", "inglés" if idioma == "en" else "castellano")
            .replace("<<mensaje>>", estado["mensaje"])
            .replace("<<datos>>", _bloque_datos(estado))
            .replace("<<fragmentos>>", _bloque_fragmentos(estado)))


def fuentes_citadas(texto: str, fragmentos: list[Fragmento]) -> list[str]:
    """Fuentes ``DOC-0X §N`` citadas en el texto que corresponden a fragmentos recuperados (§8.4)."""
    disponibles = {f.fuente for f in fragmentos}
    citadas = []
    pares = [par for corchete in _RE_CORCHETE.findall(texto) for par in _RE_CITA.findall(corchete)]
    for doc, seccion in pares:
        fuente = f"{doc} {seccion.strip()}"
        if fuente in disponibles and fuente not in citadas:
            citadas.append(fuente)
    return citadas


def sintetizar(estado: EstadoAgente) -> dict:
    """Redacta la respuesta final de las rutas de datos, documentos e híbrida."""
    c = estado["clasificacion"]
    idioma = c.idioma
    uso_sql = "consulta_sql" in estado.get("herramientas", [])
    fragmentos = estado.get("fragmentos") or []
    sql_ok = uso_sql and not estado.get("sql_error")

    if uso_sql and not sql_ok and not fragmentos:
        return {"respuesta": _ERROR_DATOS[idioma], "fuentes": [], "ruta_final": c.ruta, "nodos": ["sintetizar"]}

    respuesta = con_reintentos(modelos.llm_principal().invoke, armar_prompt(estado))
    texto = _texto(respuesta.content).strip()
    fuentes = ([_BASE[idioma]] if sql_ok else []) + fuentes_citadas(texto, fragmentos)
    if fuentes:
        texto += f"\n\n*{_ETIQUETA_FUENTE[idioma]}: {' · '.join(fuentes)}*"
    return {"respuesta": texto, "fuentes": fuentes, "ruta_final": c.ruta, "nodos": ["sintetizar"]}
