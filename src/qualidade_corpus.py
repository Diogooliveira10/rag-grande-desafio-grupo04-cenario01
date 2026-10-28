"""Nome antigo de ``src/corpus_quality.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.qualidade_corpus`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.corpus_quality``.
"""
import sys

from src import corpus_quality

if __name__ == "__main__":
    raise SystemExit(corpus_quality.main())
sys.modules[__name__] = corpus_quality
