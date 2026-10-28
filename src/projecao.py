"""Nome antigo de ``src/embedding_projection.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``import src.projecao`` devolve o próprio módulo novo, não uma cópia. Código
novo importa de ``src.embedding_projection``.
"""
import sys

from src import embedding_projection

sys.modules[__name__] = embedding_projection
