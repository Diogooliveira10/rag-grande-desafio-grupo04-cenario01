"""Nome antigo de ``src/audit_collection_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.auditar_coleta_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.audit_collection_v7``.
"""
import sys

from src import audit_collection_v7

if __name__ == "__main__":
    raise SystemExit(audit_collection_v7.main())
sys.modules[__name__] = audit_collection_v7
