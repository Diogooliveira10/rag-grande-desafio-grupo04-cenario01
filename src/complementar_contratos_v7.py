"""Nome antigo de ``src/supplement_contracts_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.complementar_contratos_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.supplement_contracts_v7``.
"""
import sys

from src import supplement_contracts_v7

if __name__ == "__main__":
    raise SystemExit(supplement_contracts_v7.main())
sys.modules[__name__] = supplement_contracts_v7
