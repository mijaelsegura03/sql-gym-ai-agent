"""Indexa los PDFs de ``rag-source/`` en Chroma (spec técnico §8.1).

Solo reindexa si cambió algún PDF (por su SHA-256) o si se pasa ``--reindexar``, para no
gastar llamadas de embeddings en cada arranque.

Uso::

    python scripts/indexar_pdfs.py [--reindexar]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from app.rag.ingesta import indexar  # noqa: E402

__all__ = ["indexar"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Indexa los PDFs de políticas en Chroma.")
    parser.add_argument("--reindexar", action="store_true", help="reindexar aunque los PDFs no hayan cambiado")
    args = parser.parse_args()
    print(indexar(forzar=args.reindexar))
    return 0


if __name__ == "__main__":
    sys.exit(main())
