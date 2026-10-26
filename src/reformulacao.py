"""Nome antigo de ``src/reformulation.py``, renomeado em 2026-10-26 (nomes de arquivo em inglês).

``import src.reformulacao`` devolve o próprio módulo novo, não uma cópia: quem
ainda importa pelo nome antigo, ou troca um valor dele num teste, mexe no
mesmo objeto. Código novo importa de ``src.reformulation``.
"""
import sys

from src import reformulation

sys.modules[__name__] = reformulation
