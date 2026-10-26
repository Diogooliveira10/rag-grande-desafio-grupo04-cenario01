"""Catálogo v7: identidade por radical, homônimos e falsos positivos."""
import json
from pathlib import Path
import sqlite3

import pytest

from src.analysis import entities
from src.analysis.suppliers import IndiceFornecedores, raiz_cnpj
from src.structured_db import Banco
from src.sql_query.detection import Consulta
from src.sql_query.execution import _where
from src.retrieve import _matches_filters
from src import query_analysis as qa


@pytest.fixture
def catalogo(tmp_path, monkeypatch):
    db = tmp_path / "base.sqlite"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE contratacoes (fornecedor TEXT, fornecedor_cnpj TEXT, objeto_normalizado TEXT, "
                  "municipio TEXT, orgao_nome TEXT, unidade_nome TEXT)")
        c.execute("CREATE TABLE pca_planos (orgao_nome TEXT, unidade_nome TEXT)")
        for nome, cnpj in [
            ("AURORAX SERVICOS LTDA", "12345678000100"),
            ("Aurorax Soluções - filial", "12345678000200"),
            ("HOMONIMA LTDA", "87654321000100"),
            ("HOMONIMA LTDA", "99999999000100"),
            ("TECNOLOGIA LIMPEZA URBANA LTDA", "11111111000100"),
            ("COMPANHIA ESTADUAL DE AGUAS E ESGOTOS CEDAE", "33352394000104"),
        ]:
            c.execute("INSERT INTO contratacoes VALUES (?, ?, ?, ?, ?, ?)",
                      (nome, cnpj, "limpeza urbana tecnologia", "Sumidouro", "MUNICIPIO DE SUMIDOURO", "Unidade"))
    path = tmp_path / "catalogo.json"
    dados = entities.gerar_catalogo_de_orgaos(path, banco=Banco(sqlite_path=db))
    monkeypatch.setattr(entities, "ORGAOS_CORPUS_PATH", path)
    entities._catalogo_de_orgaos.cache_clear()
    entities._palavras_dos_objetos.cache_clear()
    yield dados, db
    entities._catalogo_de_orgaos.cache_clear()
    entities._palavras_dos_objetos.cache_clear()


def test_variantes_e_filiais_resolvem_a_mesma_identidade(catalogo):
    dados, db = catalogo
    indice = IndiceFornecedores(dados)
    nome = indice.valor(["aurorax"])
    assert nome is not None
    assert indice.raizes(nome) == ("12345678",)
    assert len(dados["fornecedores_identidades"]["12345678"]["cnpjs"]) == 2
    assert dados["apelidos_fornecedor"]["aurorax"] == nome
    assert qa.analisar_query("Contratos da Aurorax", modo="regras").filtros.filtros_analiticos["fornecedor"] == nome
    consulta = Consulta(tabela="contratacoes", tipo="listagem", todas_as_situacoes=True,
                        filtros_analiticos=(("fornecedor", nome),))
    where, params = _where(consulta, [])
    with sqlite3.connect(db) as c:
        assert c.execute("SELECT COUNT(*) FROM contratacoes" + where, params).fetchone()[0] == 2
    for cnpj in ("12345678000100", "12345678000200"):
        assert _matches_filters({"fornecedor": "Grafia diferente", "fornecedor_cnpj": cnpj},
                                {"fornecedor": {"op": "contains", "value": nome}})
    assert _matches_filters({"extras": {"fornecedor": "Aurorax Soluções - filial"}},
                            {"fornecedor": {"op": "contains", "value": nome}})
    assert not _matches_filters({"fornecedor": nome, "fornecedor_cnpj": "87654321000100"},
                                {"fornecedor": {"op": "contains", "value": nome}})


def test_homonimos_nao_sao_fundidos(catalogo):
    indice = IndiceFornecedores(catalogo[0])
    assert indice.valor(["homonima"]) is None
    assert indice.raizes("HOMONIMA LTDA") == ()
    assert "homonima" not in catalogo[0]["apelidos_fornecedor"]


@pytest.mark.parametrize("pergunta", [
    "Contratos da limpeza urbana", "Contratos com a empresa tecnologia",
    "Contratos da TECNOLOGIA", "Quanto custa a limpeza?", "Quais serviços da CEDAE?",
    "Contratos do Município de Sumidouro", "Fornecedor de CNPJ 12.345.678/0001-00",
])
def test_objetos_orgaos_e_numeros_nao_viram_nome_de_fornecedor(catalogo, pergunta):
    assert "fornecedor" not in qa.analisar_query(pergunta, modo="regras").filtros.filtros_analiticos


def test_cnpj_explicito_permanece_exato(catalogo):
    consulta = Consulta(tabela="contratacoes", tipo="listagem", papel_cnpj="fornecedor",
                        filtros_analiticos=(("cnpj", "12345678000100"),))
    where, params = _where(consulta, [])
    assert "fornecedor_cnpj = ?" in where
    assert params == ["12345678000100"]
    assert raiz_cnpj("123.456.789-00") is None
    assert raiz_cnpj("00000000000664") is None


def test_cache_de_analise_muda_com_identidades(catalogo, monkeypatch):
    antes = qa._analysis_key("Contratos da Aurorax")
    path = entities.ORGAOS_CORPUS_PATH
    dados = json.loads(path.read_text(encoding="utf-8"))
    dados["fornecedores_sha256"] = "outra-assinatura"
    path.write_text(json.dumps(dados), encoding="utf-8")
    entities._catalogo_de_orgaos.cache_clear()
    assert qa._analysis_key("Contratos da Aurorax") != antes


def test_integridade_do_catalogo_inclui_identidades_e_apelidos(catalogo):
    from src.snapshot import _validar_catalogo, SnapshotValidationError
    path = entities.ORGAOS_CORPUS_PATH
    _validar_catalogo(path, catalogo[1])
    dados = json.loads(path.read_text(encoding="utf-8"))
    dados["apelidos_fornecedor"]["limpeza"] = "AURORAX SERVICOS LTDA"
    path.write_text(json.dumps(dados), encoding="utf-8")
    with pytest.raises(SnapshotValidationError, match="catalogo_divergente"):
        _validar_catalogo(path, catalogo[1])


@pytest.mark.parametrize("arquivo", ["golden_set.jsonl", "retrieval_set.jsonl"])
def test_avaliacoes_nao_ganham_filtros_espurios(arquivo):
    # Só V1 e I04 citam a empresa pelo nome ("Cristália", "Gente Seguradora").
    # I03/I08 citam CNPJ: não são nomes. Na I04, o filtro mantém o contrato
    # 75/2025 em 1º na busca (a grafia "S.A" do vetor casa pela raiz do CNPJ).
    path = Path(__file__).resolve().parents[1] / "eval" / arquivo
    encontrados = {}
    for linha in path.read_text(encoding="utf-8").splitlines():
        caso = json.loads(linha)
        fornecedor = qa.analisar_query(caso["pergunta"], modo="regras").filtros.filtros_analiticos.get("fornecedor")
        if fornecedor:
            encontrados[caso["id"]] = fornecedor
    assert encontrados == ({"V1": "CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS LTDA"}
                            if arquivo == "golden_set.jsonl" else {"I04": "GENTE SEGURADORA SA"})


@pytest.mark.parametrize("nome,procurado,exata,esperado", [
    ("GENTE SEGURADORA S.A", "GENTE SEGURADORA SA", True, True),
    ("GENTE SEGURADORA S/A", "GENTE SEGURADORA SA", True, True),
    ("CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS LTDA.", "CRISTALIA PRODUTOS QUIMICOS FARMACEUTICOS LTDA", True, True),
    ("OUTRA SEGURADORA SA", "GENTE SEGURADORA SA", True, False),
    ("GENTE SEGURADORA SA", "GENTE", True, False),
    ("GENTE SEGURADORA SA", "GENTE", False, True),
    ("AGENTE SEGURADORA SA", "GENTE SEGURADORA", False, False),
])
def test_mesma_razao_social_ignora_pontuacao_e_forma_juridica(nome, procurado, exata, esperado):
    from src.analysis.suppliers import mesma_razao_social
    assert mesma_razao_social(nome, procurado, exata=exata) is esperado


def test_filtro_de_fornecedor_com_igualdade_usa_a_raiz_do_cnpj(monkeypatch):
    """A análise pede "==": a grafia do vetor ("S.A") casa pela mesma raiz do CNPJ."""
    import src.retrieve as retrieve_module
    from src.analysis import suppliers

    monkeypatch.setattr(suppliers, "raizes_do_fornecedor",
                        lambda nome: ("90180605",) if "GENTE" in nome.upper() else ())
    filtro = {"fornecedor": {"operator": "==", "value": "GENTE SEGURADORA SA", "optional": False}}
    assert retrieve_module._matches_filters({"fornecedor": "GENTE SEGURADORA S.A"}, filtro)
    assert not retrieve_module._matches_filters({"fornecedor": "OUTRA SEGURADORA SA"}, filtro)

