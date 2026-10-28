"""Nome antigo de ``src/recover_corpus_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.recuperar_corpus_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.recover_corpus_v7``.
"""
import sys

from src import recover_corpus_v7

if __name__ == "__main__":
    raise SystemExit(recover_corpus_v7.main())
sys.modules[__name__] = recover_corpus_v7
