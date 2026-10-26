"""Nome antigo de ``src/complete_vectors_v7.py``, renomeado em 2026-10-26 (nomes de arquivo em inglês).

``python -m src.completar_vetores_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.complete_vectors_v7``.
"""
import sys

from src import complete_vectors_v7

if __name__ == "__main__":
    raise SystemExit(complete_vectors_v7.main())
sys.modules[__name__] = complete_vectors_v7
