"""Nome antigo de ``src/collection_policy.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``import src.coleta_policy`` devolve o próprio módulo novo, não uma cópia. Código
novo importa de ``src.collection_policy``.
"""
import sys

from src import collection_policy

sys.modules[__name__] = collection_policy
