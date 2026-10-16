"""Nodo ``sintetizar`` (spec técnico §5.2, §5.3, §8.4; RF-05, RF-07, RF-10, RF-11, RF-14, RF-18, RF-19, RF-22, RF-23).

Redacta la respuesta con lo que reunió el agente de lectura (las consultas a la base y los fragmentos de
los documentos), en el idioma del mensaje. Fija la ruta según las herramientas que usó el agente:
solo ``consulta_sql`` → ``datos``; solo ``busqueda_documentos`` → ``documentos``; las dos → ``hibrida``;
ninguna → ``lectura``. El LLM cita con las etiquetas ``[DOC-0X §N, p. P]`` y el código, nunca el LLM:

- guarda en ``citas`` las que corresponden a fragmentos recuperados (``DOC-0X §N``, RF-10);
- las saca del texto, porque el usuario las ve en el detalle y no en la respuesta (RF-10);
- arma las **fuentes**: "base de datos" si la consulta se ejecutó y el título de cada documento
  citado, sin secciones (RF-19).

Si ninguna consulta a la base terminó bien y no hay fragmentos, la respuesta es un mensaje fijo y
comprensible, sin trazas técnicas y sin llamar al LLM (RF-23). Si el agente no consultó ninguna fuente,
también responde con un mensaje fijo (RF-05, RF-11).
"""

from __future__ import annotations

import json
import re

from app.agent.estado import EstadoAgente
from app.agent.nodos import cargar_prompt, modelos
from app.rag.buscador import Fragmento

_ERROR_DATOS = {
    "es": "No pude obtener esa información de la base de datos en este momento. Probá reformular la pregunta "
          "o volvé a intentar en unos minutos.",
    "en": "I couldn't get that information from the database right now. Try rephrasing the question or try "
          "again in a few minutes.",
}
_SIN_INFORMACION = {
    "es": "No encontré información sobre eso en la base de datos ni en los documentos del gimnasio. Probá "
          "reformular la pregunta con más detalle.",
    "en": "I couldn't find information about that in the gym's database or documents. Try rephrasing the "
          "question with more detail.",
}
_ETIQUETA_FUENTE = {"es": "Fuente", "en": "Source"}
_BASE = {"es": "base de datos", "en": "database"}
_RE_CORCHETE = re.compile(r"\[([^\]]*DOC-\d{2}[^\]]*)\]")
_RE_CITA = re.compile(r"(DOC-\d{2})\s+(§[\wÁÉÍÓÚáéíóúñ]+)")
_RE_ETIQUETA = re.compile(r"[ \t]*\[[^\]]*DOC-\d{2}[^\]]*\]")
_RE_ESPACIO_PUNTUACION = re.compile(r"[ \t]+([.,;:)])")


def _texto(contenido) -> str:
    if isinstance(contenido, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in contenido)
    return str(contenido or "")


def ruta_de_lectura(herramientas: list[str]) -> str:
    """Ruta informada de una lectura según las herramientas que usó el agente (§5.3, RF-14)."""
    sql, docs = "consulta_sql" in herramientas, "busqueda_documentos" in herramientas
    return "hibrida" if sql and docs else "datos" if sql else "documentos" if docs else "lectura"


def _bloque_filas(c) -> str:
    filas = c.filas or []
    total = c.total_filas or 0
    if not filas:
        return "(la consulta no devolvió ninguna fila)"
    encabezado = f"Filas: {len(filas)}" + (f" (se muestran 50 de {total} en total)" if total > len(filas) else "")
    return encabezado + "\n" + "\n".join(json.dumps(f, ensure_ascii=False, default=str) for f in filas)


def _bloque_datos(estado: EstadoAgente) -> str:
    if "consulta_sql" not in estado.get("herramientas", []):
        return "(no se consultó la base de datos)"
    exitosas = [c for c in estado.get("consultas") or [] if c.ok]
    if not exitosas:
        return "(la consulta a la base falló; no hay datos disponibles)"
    if len(exitosas) == 1:
        return _bloque_filas(exitosas[0])
    return "\n\n".join(f"Consulta {i}:\n{_bloque_filas(c)}" for i, c in enumerate(exitosas, 1))


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


def quitar_citas(texto: str) -> str:
    """Saca las etiquetas ``[DOC-0X …]`` del texto y el espacio que quedaba antes de la puntuación."""
    return _RE_ESPACIO_PUNTUACION.sub(r"\1", _RE_ETIQUETA.sub("", texto)).strip()


def documentos_citados(citas: list[str], fragmentos: list[Fragmento]) -> list[str]:
    """Títulos de los documentos de las ``citas``, sin repetir y en el orden en que se citaron."""
    titulo = {f.fuente: f.documento for f in fragmentos}
    return list(dict.fromkeys(titulo[c] for c in citas))


def sintetizar(estado: EstadoAgente) -> dict:
    """Redacta la respuesta final de una consulta de lectura con lo que reunió el agente."""
    idioma = estado["clasificacion"].idioma
    herramientas = estado.get("herramientas") or []
    ruta = ruta_de_lectura(herramientas)
    uso_sql = "consulta_sql" in herramientas
    fragmentos = estado.get("fragmentos") or []
    sql_ok = any(c.ok for c in estado.get("consultas") or [])

    if ruta == "lectura":  # el agente no consultó ninguna fuente
        return {"respuesta": _SIN_INFORMACION[idioma], "fuentes": [], "citas": [], "ruta_final": ruta,
                "nodos": ["sintetizar"]}
    if uso_sql and not sql_ok and not fragmentos:
        return {"respuesta": _ERROR_DATOS[idioma], "fuentes": [], "citas": [], "ruta_final": ruta,
                "nodos": ["sintetizar"]}

    respuesta = modelos.invocar("principal", armar_prompt(estado))
    crudo = _texto(respuesta.content).strip()
    citas = fuentes_citadas(crudo, fragmentos)
    texto = quitar_citas(crudo)
    fuentes = ([_BASE[idioma]] if sql_ok else []) + documentos_citados(citas, fragmentos)
    if fuentes:
        texto += f"\n\n*{_ETIQUETA_FUENTE[idioma]}: {' · '.join(fuentes)}*"
    return {"respuesta": texto, "fuentes": fuentes, "citas": citas, "ruta_final": ruta, "nodos": ["sintetizar"]}
