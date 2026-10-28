"""Nome antigo de ``src/watch_registry_v7.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.acompanhar_cadastro_v7`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.watch_registry_v7``.
"""
import sys

from src import watch_registry_v7

if __name__ == "__main__":
    raise SystemExit(watch_registry_v7.main())
sys.modules[__name__] = watch_registry_v7
