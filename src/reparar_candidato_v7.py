"""Nome antigo de ``src/repair_candidate_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.reparar_candidato_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.repair_candidate_v7``.
"""
import sys

from src import repair_candidate_v7

if __name__ == "__main__":
    raise SystemExit(repair_candidate_v7.main())
sys.modules[__name__] = repair_candidate_v7
