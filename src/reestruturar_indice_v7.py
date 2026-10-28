"""Nome antigo de ``src/restructure_index_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.reestruturar_indice_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.restructure_index_v7``.
"""
import sys

from src import restructure_index_v7

if __name__ == "__main__":
    raise SystemExit(restructure_index_v7.main())
sys.modules[__name__] = restructure_index_v7
