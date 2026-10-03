"""Extracción y chunking de los PDFs, sin llamar a la API (spec técnico §8.1)."""

from __future__ import annotations

import pytest

from app.rag.buscador import Fragmento
from app.rag.ingesta import chunkear, extraer_documento, pdfs, tabla_a_markdown


@pytest.fixture(scope="module")
def chunks_por_doc():
    return {p.name[:2]: chunkear(extraer_documento(p)) for p in pdfs()}


def test_cuatro_pdfs():
    assert len(pdfs()) == 4


def test_metadatos_completos(chunks_por_doc):
    for chunks in chunks_por_doc.values():
        assert chunks
        for c in chunks:
            m = c.metadatos()
            assert m["doc_id"].startswith("DOC-0") and m["seccion"] and m["pagina"] >= 1 and m["chunk_id"]
    ids = [c.chunk_id for cs in chunks_por_doc.values() for c in cs]
    assert len(ids) == len(set(ids))


def test_sin_encabezados_ni_pies(chunks_por_doc):
    for chunks in chunks_por_doc.values():
        for c in chunks:
            assert "Versión 1.0 · Octubre 2026" not in c.texto
            assert "Página " not in c.texto


def test_chunk_por_seccion_numerada(chunks_por_doc):
    secciones = {c.seccion for c in chunks_por_doc["01"]}
    for n in range(1, 10):
        assert any(s.startswith(f"{n}. ") for s in secciones), f"falta la sección {n} de DOC-01"
    assert "Resumen" in secciones


def test_lista_numerada_no_es_seccion(chunks_por_doc):
    # DOC-04 §7 tiene una lista "1. Detener la actividad…" que no debe cortar secciones
    secciones = [c.seccion for c in chunks_por_doc["04"]]
    assert not any("Detener la actividad" in s for s in secciones)
    protocolo = next(c for c in chunks_por_doc["04"] if c.seccion.startswith("7."))
    assert "Detener la actividad" in protocolo.texto


def test_preguntas_frecuentes_como_chunks_propios(chunks_por_doc):
    faq = [c for c in chunks_por_doc["04"] if "Preguntas frecuentes" in c.seccion]
    assert len(faq) == 7
    apto = next(c for c in faq if "¿Cuánto dura el apto médico?" in c.seccion)
    assert "12 meses" in apto.texto


def test_tabla_de_motivos_de_rechazo_conserva_filas(chunks_por_doc):
    motivos = next(c for c in chunks_por_doc["01"] if c.seccion.startswith("4."))
    filas = [l for l in motivos.texto.splitlines() if l.startswith("| Cuota vencida")]
    assert len(filas) == 1
    fila = filas[0]
    assert "no hay período de gracia" in fila and "MercadoPago" in fila  # explicación y solución juntas
    assert "| Sin apto médico |" in motivos.texto


def test_secciones_largas_se_subdividen(chunks_por_doc):
    for chunks in chunks_por_doc.values():
        for c in chunks:
            cuerpo = c.texto.split("\n", 1)[1]
            assert len(cuerpo) <= 1500


def test_tabla_a_markdown():
    md = tabla_a_markdown([["A", "B"], ["uno\ndos", None]])
    assert md.splitlines() == ["| A | B |", "|---|---|", "| uno dos |  |"]


def test_etiqueta_de_cita():
    f = Fragmento(doc_id="DOC-01", seccion="4. Motivos de rechazo de acceso", pagina=2, texto="x")
    assert f.etiqueta == "[DOC-01 §4, p. 2]"
    assert f.fuente == "DOC-01 §4"
