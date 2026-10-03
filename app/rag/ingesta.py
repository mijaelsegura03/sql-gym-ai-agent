"""Ingesta de los PDFs de políticas en Chroma (spec técnico §8.1).

Pasos:

1. Por cada PDF de ``rag-source/``, el ``doc_id`` sale del nombre (``01_…`` → ``DOC-01``).
2. Con PyMuPDF se extraen los bloques de texto de cada página, en orden de lectura. Las tablas se
   detectan con ``find_tables()`` y se convierten a Markdown, para que cada fila conserve juntas sus
   columnas (por ejemplo, *motivo | qué significa | cómo se resuelve* de DOC-01 §4).
3. Se descartan los encabezados y pies repetidos (``GIMNASIO``, título, ``DOC-0X · Versión …``, ``Página N``).
4. Chunking por sección: se corta en los títulos numerados (``N. Título``), además del bloque
   "Resumen" y de cada pregunta de "Preguntas frecuentes", que quedan como chunks propios. Las
   secciones de más de 1.500 caracteres se subdividen (1.000 caracteres, 150 de solapamiento).
5. Metadatos por chunk: ``doc_id``, ``titulo_doc``, ``seccion``, ``pagina`` y ``chunk_id``.
6. Embeddings con ``RETRIEVAL_DOCUMENT`` y alta en la colección ``politicas`` de Chroma.
7. El SHA-256 de cada PDF se guarda en los metadatos de la colección: si no cambió ninguno, no
   se vuelve a indexar (no se llama a la API de embeddings).

Las funciones de extracción y chunking (:func:`extraer_documento`, :func:`chunkear`) no usan la
red, así que se pueden probar sin API key.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import RAIZ, get_settings

CARPETA_PDFS = RAIZ / "rag-source"
COLECCION = "politicas"
MAX_SECCION = 1500
TAM_SUBCHUNK = 1000
SOLAPAMIENTO = 150

_RE_TITULO = re.compile(r"^(\d{1,2})\.\s+(\S.*)$")
_RE_PIE = re.compile(r"^(DOC-\d{2}\s*·.*|Página\s+\d+)$")
_MARGEN_SUP, _MARGEN_INF = 45, 800  # pt: encabezado arriba, pie abajo (páginas A4)


@dataclass
class Elemento:
    """Un párrafo, viñeta, título o tabla (ya en Markdown) de una página."""

    texto: str
    pagina: int
    tipo: str = "parrafo"  # parrafo | vineta | tabla


@dataclass
class Documento:
    """Un PDF extraído: identificador, título y elementos en orden de lectura."""

    doc_id: str
    titulo: str
    archivo: str
    elementos: list[Elemento] = field(default_factory=list)


@dataclass
class Chunk:
    """Fragmento indexable con sus metadatos (§8.1 paso 5)."""

    chunk_id: str
    doc_id: str
    titulo_doc: str
    seccion: str
    pagina: int
    texto: str

    def metadatos(self) -> dict:
        return {"doc_id": self.doc_id, "titulo_doc": self.titulo_doc, "seccion": self.seccion,
                "pagina": self.pagina, "chunk_id": self.chunk_id}


# ---------------------------------------------------------------------------
# Extracción
# ---------------------------------------------------------------------------
def doc_id_de(path: Path) -> str:
    """``01_Reglamento….pdf`` → ``DOC-01``."""
    m = re.match(r"^(\d{2})_", path.name)
    if not m:
        raise ValueError(f"El nombre del PDF debe empezar con dos dígitos y guion bajo: {path.name}")
    return f"DOC-{m.group(1)}"


def tabla_a_markdown(filas: list[list[str | None]]) -> str:
    """Convierte las filas de una tabla en una tabla Markdown (la primera fila es el encabezado)."""
    def celda(c: str | None) -> str:
        return re.sub(r"\s+", " ", (c or "")).strip().replace("|", "/")

    filas = [[celda(c) for c in fila] for fila in filas if any(c for c in fila)]
    if not filas:
        return ""
    ancho = max(len(f) for f in filas)
    filas = [f + [""] * (ancho - len(f)) for f in filas]
    lineas = ["| " + " | ".join(filas[0]) + " |", "|" + "---|" * ancho]
    lineas += ["| " + " | ".join(f) + " |" for f in filas[1:]]
    return "\n".join(lineas)


def _dentro(bbox_bloque: tuple, bbox_tabla: tuple) -> bool:
    x0, y0, x1, y1 = bbox_bloque
    tx0, ty0, tx1, ty1 = bbox_tabla
    cy = (y0 + y1) / 2
    return ty0 - 2 <= cy <= ty1 + 2 and x1 >= tx0 and x0 <= tx1


def extraer_documento(path: Path) -> Documento:
    """Extrae los elementos de un PDF en orden de lectura, con las tablas en Markdown."""
    pdf = pymupdf.open(path)
    titulo = ""
    elementos: list[Elemento] = []
    for pagina in pdf:
        n = pagina.number + 1
        tablas = [(t.bbox, tabla_a_markdown(t.extract())) for t in pagina.find_tables().tables]
        piezas: list[tuple[float, Elemento]] = [(bbox[1], Elemento(md, n, "tabla")) for bbox, md in tablas if md]
        for x0, y0, x1, y1, texto, *_ in pagina.get_text("blocks"):
            if y1 <= _MARGEN_SUP or y0 >= _MARGEN_INF:
                continue  # encabezado o pie repetido
            if any(_dentro((x0, y0, x1, y1), bbox) for bbox, _ in tablas):
                continue
            lineas = [l.strip() for l in texto.splitlines() if l.strip()]
            lineas = [l for l in lineas if not _RE_PIE.match(l)]
            if not lineas:
                continue
            if lineas[0] == "•":
                piezas.append((y0, Elemento("- " + " ".join(lineas[1:]), n, "vineta")))
                continue
            if lineas[0] == "Resumen" and len(lineas) > 1:  # título y párrafo en el mismo bloque
                piezas.append((y0, Elemento("Resumen", n)))
                piezas.append((y0 + 0.1, Elemento(" ".join(lineas[1:]), n)))
                continue
            piezas.append((y0, Elemento(" ".join(lineas), n)))
        piezas.sort(key=lambda p: p[0])
        elementos.extend(e for _, e in piezas)
    if elementos:
        titulo = elementos[0].texto
    return Documento(doc_id=doc_id_de(path), titulo=titulo, archivo=path.name, elementos=elementos)


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
@dataclass
class _Seccion:
    nombre: str
    pagina: int
    partes: list[str] = field(default_factory=list)
    paginas: list[int] = field(default_factory=list)  # página de cada parte

    def agregar(self, texto: str, pagina: int) -> None:
        self.partes.append(texto)
        self.paginas.append(pagina)

    def pagina_de(self, fragmento: str) -> int:
        """Página del primer elemento de la sección que aparece en ``fragmento``."""
        for texto, pagina in zip(self.partes, self.paginas):
            if texto[:60] and texto[:60] in fragmento:
                return pagina
        return self.pagina

    def texto(self) -> str:
        return "\n".join(self.partes).strip()


def _secciones(doc: Documento) -> list[_Seccion]:
    """Agrupa los elementos en secciones: Resumen, títulos numerados y cada pregunta frecuente."""
    secciones: list[_Seccion] = []
    actual = _Seccion("Presentación", doc.elementos[0].pagina if doc.elementos else 1)
    esperado = 1
    en_faq = False
    titulo_faq = ""
    for e in doc.elementos:
        m = _RE_TITULO.match(e.texto) if e.tipo == "parrafo" else None
        if e.tipo == "parrafo" and e.texto == "Resumen":
            # El título y el subtítulo del documento quedan dentro del Resumen como contexto
            previo = actual.texto()
            actual = _Seccion("Resumen", e.pagina, [previo] if previo else [], [e.pagina] if previo else [])
            continue
        if m and int(m.group(1)) == esperado and len(e.texto) < 90:
            secciones.append(actual)
            esperado += 1
            nombre = e.texto
            en_faq = "preguntas frecuentes" in m.group(2).lower()
            titulo_faq = nombre if en_faq else ""
            actual = _Seccion(nombre, e.pagina)
            continue
        if en_faq and e.tipo == "parrafo" and e.texto.endswith("?") and len(e.texto) < 160:
            secciones.append(actual)
            actual = _Seccion(f"{titulo_faq} — {e.texto}", e.pagina, [e.texto], [e.pagina])
            continue
        actual.agregar(e.texto, e.pagina)
    secciones.append(actual)
    return [s for s in secciones if s.texto()]


def _numero_seccion(nombre: str) -> str:
    m = re.match(r"^(\d+)\.", nombre)
    return f"§{m.group(1)}" if m else f"§{nombre.split(' —')[0]}"


def chunkear(doc: Documento) -> list[Chunk]:
    """Divide un documento en chunks por sección (§8.1 paso 4) con sus metadatos."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=TAM_SUBCHUNK, chunk_overlap=SOLAPAMIENTO,
                                              separators=["\n", ". ", " ", ""])
    chunks: list[Chunk] = []
    for sec in _secciones(doc):
        cuerpo = sec.texto()
        partes = splitter.split_text(cuerpo) if len(cuerpo) > MAX_SECCION else [cuerpo]
        for i, parte in enumerate(partes):
            sufijo = f" (parte {i + 1}/{len(partes)})" if len(partes) > 1 else ""
            encabezado = f"{doc.doc_id} — {doc.titulo} — {sec.nombre}{sufijo}"
            chunks.append(Chunk(
                chunk_id=f"{doc.doc_id}-{len(chunks):03d}",
                doc_id=doc.doc_id,
                titulo_doc=doc.titulo,
                seccion=sec.nombre,
                pagina=sec.pagina,
                texto=f"{encabezado}\n{parte}",
            ))
    return chunks


def pdfs() -> list[Path]:
    """PDFs del corpus, en orden."""
    return sorted(CARPETA_PDFS.glob("[0-9][0-9]_*.pdf"))


def hashes_pdfs() -> dict[str, str]:
    """SHA-256 de cada PDF del corpus, por nombre de archivo."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in pdfs()}


# ---------------------------------------------------------------------------
# Indexación en Chroma
# ---------------------------------------------------------------------------
def cliente_chroma():
    """Cliente persistente de Chroma en ``CHROMA_DIR``."""
    import chromadb
    from chromadb.config import Settings as ChromaSettings

    return chromadb.PersistentClient(path=str(get_settings().chroma_path),
                                     settings=ChromaSettings(anonymized_telemetry=False))


def necesita_indexar() -> bool:
    """True si la colección no existe o algún PDF cambió desde la última indexación."""
    try:
        col = cliente_chroma().get_collection(COLECCION)
    except Exception:  # noqa: BLE001 - la colección no existe
        return True
    guardados = json.loads((col.metadata or {}).get("hashes", "{}"))
    return guardados != hashes_pdfs() or col.count() == 0


def indexar(forzar: bool = False) -> str:
    """Indexa los PDFs si hace falta (o siempre, con ``forzar``). Devuelve un resumen en texto."""
    if not forzar and not necesita_indexar():
        return "sin cambios en los PDFs (no se reindexa)"
    from app.llm import embeber_documentos

    chunks = [c for p in pdfs() for c in chunkear(extraer_documento(p))]
    vectores = embeber_documentos([c.texto for c in chunks])
    cliente = cliente_chroma()
    try:
        cliente.delete_collection(COLECCION)
    except Exception:  # noqa: BLE001 - no existía
        pass
    col = cliente.create_collection(
        COLECCION,
        metadata={"hnsw:space": "cosine", "hashes": json.dumps(hashes_pdfs()),
                  "modelo": get_settings().gemini_embedding_model},
        embedding_function=None,
    )
    col.add(ids=[c.chunk_id for c in chunks], documents=[c.texto for c in chunks],
            metadatas=[c.metadatos() for c in chunks], embeddings=vectores)
    return f"{len(chunks)} chunks de {len(pdfs())} PDFs indexados"
