"""Nome antigo de ``src/direct_build.py``, renomeado em 2026-10-27 (nomes de arquivo em inglês).

``python -m src.build_direto`` continua funcionando, e o import pelo
nome antigo devolve o próprio módulo novo. Código novo usa ``src.direct_build``.
"""
import sys

from src import direct_build

if __name__ == "__main__":
    raise SystemExit(direct_build.main())
sys.modules[__name__] = direct_build
