"""Nome antigo de ``src/build_faiss_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.montar_faiss_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.build_faiss_v7``.
"""
import sys

from src import build_faiss_v7

if __name__ == "__main__":
    raise SystemExit(build_faiss_v7.main())
sys.modules[__name__] = build_faiss_v7
