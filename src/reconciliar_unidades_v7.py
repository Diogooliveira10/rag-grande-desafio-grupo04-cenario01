"""Nome antigo de ``src/reconcile_units_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.reconciliar_unidades_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.reconcile_units_v7``.
"""
import sys

from src import reconcile_units_v7

if __name__ == "__main__":
    raise SystemExit(reconcile_units_v7.main())
sys.modules[__name__] = reconcile_units_v7
