"""Nome antigo de ``src/merge_bases_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.incorporar_bases_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.merge_bases_v7``.
"""
import sys

from src import merge_bases_v7

if __name__ == "__main__":
    raise SystemExit(merge_bases_v7.main())
sys.modules[__name__] = merge_bases_v7
