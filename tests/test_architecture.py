"""Regras de dependência entre as camadas (Clean Architecture).

O domínio (src/dominio) só depende da biblioteca padrão, das entidades
(src/schema.py) e de si mesmo. Se um teste daqui falhar, alguém fez o domínio
depender de infraestrutura: mova a dependência para o adaptador, que chama o
domínio, em vez de importar o adaptador no domínio.
"""

import ast
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
DOMINIO = RAIZ / "src" / "dominio"
_PERMITIDOS_NO_DOMINIO = {"src.schema", "src.dominio"}
# As entidades podem usar pydantic (validação dos modelos), e nada do projeto.
_PERMITIDOS_NAS_ENTIDADES = {"pydantic"}


def _importados(arquivo: Path) -> set[str]:
    arvore = ast.parse(arquivo.read_text(encoding="utf-8"))
    nomes: set[str] = set()
    for no in ast.walk(arvore):
        if isinstance(no, ast.Import):
            nomes.update(alias.name for alias in no.names)
        elif isinstance(no, ast.ImportFrom) and no.module and not no.level:
            nomes.add(no.module)
    return nomes


def _externo(nome: str) -> bool:
    raiz = nome.split(".")[0]
    return raiz != "__future__" and raiz not in sys.stdlib_module_names


def _permitido(nome: str, permitidos: set[str]) -> bool:
    return any(nome == p or nome.startswith(p + ".") for p in permitidos)


@pytest.mark.parametrize("arquivo", sorted(DOMINIO.glob("*.py")), ids=lambda p: p.name)
def test_dominio_nao_depende_de_infraestrutura(arquivo):
    proibidos = sorted(n for n in _importados(arquivo) if _externo(n) and not _permitido(n, _PERMITIDOS_NO_DOMINIO))
    assert not proibidos, f"{arquivo.name} importa {proibidos}"


def test_entidades_nao_dependem_do_resto_do_projeto():
    importados = _importados(RAIZ / "src" / "schema.py")
    proibidos = sorted(n for n in importados if _externo(n) and not _permitido(n, _PERMITIDOS_NAS_ENTIDADES))
    assert not proibidos, f"src/schema.py importa {proibidos}"


def test_caso_de_uso_nao_importa_nome_privado_de_outro_modulo():
    # O pipeline usava retrieve._filtro_opcional: detalhe interno da busca
    # vazando para o caso de uso. Quem precisa de algo de outro módulo usa a
    # parte pública dele (ou a porta, em src/portas.py).
    arvore = ast.parse((RAIZ / "src" / "pipeline.py").read_text(encoding="utf-8"))
    privados = sorted(f"{no.module}.{alias.name}" for no in ast.walk(arvore)
                      if isinstance(no, ast.ImportFrom) and no.module and no.module.startswith("src")
                      for alias in no.names if alias.name.startswith("_"))
    assert not privados, f"src/pipeline.py importa nomes privados: {privados}"


def test_dominio_existe_e_tem_as_regras_extraidas():
    # Guarda contra apagar o pacote e o teste acima passar vazio.
    assert {p.stem for p in DOMINIO.glob("*.py")} >= {"__init__", "confianca", "fusao", "situacao", "texto", "valores_e_periodos"}


def test_caminho_do_catalogo_de_orgaos_aponta_para_a_raiz():
    # O módulo de entidades saiu de src/ para src/analise/: o caminho relativo
    # ao arquivo mudou de nível. Os testes trocam o caminho, então só este
    # confere o de verdade.
    from src.analise import entidades
    assert entidades.ORGAOS_CORPUS_PATH == RAIZ / "data" / "config" / "orgaos_corpus.json"
