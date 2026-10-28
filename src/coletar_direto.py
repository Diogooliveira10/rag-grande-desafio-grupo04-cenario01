"""Nome antigo de ``src/direct_collection.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.coletar_direto`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.direct_collection``.
"""
import sys

from src import direct_collection

if __name__ == "__main__":
    raise SystemExit(direct_collection.main())
sys.modules[__name__] = direct_collection
