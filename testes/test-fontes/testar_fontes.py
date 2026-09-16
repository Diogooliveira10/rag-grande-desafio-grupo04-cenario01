"""Entrada curta para a checagem das fontes do cenário 01."""

try:
    from testar_fontes_cenario01 import main
except ImportError:  # permite import como pacote
    from .testar_fontes_cenario01 import main


if __name__ == "__main__":
    raise SystemExit(main())
