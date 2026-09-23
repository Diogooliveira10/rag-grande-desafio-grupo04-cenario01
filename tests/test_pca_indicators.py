import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.pca_indicators import brl, eh_csv_pca, indicadores_pca
from src.prepare_documents import _normalize_date_value, prepare_documents
from src.schema import ChunkMetadata


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "corpus_manifest.yaml"

CABECALHO = (
    "Unidade Responsável;Categoria do Item;Código da Classificação Superior (Classe/Grupo);"
    "Nome da Classificação Superior (Classe/Grupo);Descrição do Item;Valor Total Estimado (R$);Data Desejada"
)
LINHAS = [
    "POLÍCIA MILITAR;Material;6515;SUPRIMENTOS PARA USO MEDICO E CIRURGICO;Luva cirúrgica;1.000,50;15/03/2025",
    "POLÍCIA MILITAR;Serviço;0317;SERVICOS DE ACESSO A INTERNET;Link dedicado;3000,00;15/12/2025",
    "SECRETARIA DE SAÚDE;Material;6515;SUPRIMENTOS PARA USO MEDICO E CIRURGICO;Seringa;6.000,00;01/03/2025",
]


def _linhas() -> list[dict[str, str]]:
    colunas = CABECALHO.split(";")
    return [dict(zip(colunas, linha.split(";"))) for linha in LINHAS]


def _por_id(indicadores):
    return {indicador.id.rsplit("-", 1)[0]: indicador for indicador in indicadores}


# --------------------------------------------------------------------------
# Unitário: cálculo dos indicadores
# --------------------------------------------------------------------------


def test_brl_formata_em_reais():
    assert brl(Decimal("61119549484.0364")) == "R$ 61.119.549.484,04"
    assert brl(Decimal("0")) == "R$ 0,00"


def test_eh_csv_pca_exige_as_colunas_do_pca():
    assert eh_csv_pca(CABECALHO.split(";"))
    assert not eh_csv_pca(["ID Licitação", "Objeto", "Valor Total Homologado (R$)"])


def test_visao_geral_soma_todos_os_itens():
    visao = indicadores_pca(_linhas(), orgao="Estado do Rio de Janeiro")[0]

    assert visao.dimensao == "visao_geral"
    assert visao.metadados["valor_total"] == pytest.approx(10000.50)
    assert visao.metadados["itens"] == 3
    assert "R$ 10.000,50" in visao.texto
    assert "Estado do Rio de Janeiro" in visao.texto


def test_um_indicador_por_categoria_unidade_classe_e_mes_mais_rankings():
    indicadores = indicadores_pca(_linhas())
    dimensoes = [indicador.dimensao for indicador in indicadores]

    assert dimensoes.count("categoria") == 2
    assert dimensoes.count("unidade") == 2
    assert dimensoes.count("classe") == 2
    assert dimensoes.count("mes") == 2
    assert {"ranking_unidade", "ranking_classe"} <= set(dimensoes)
    assert len({indicador.id for indicador in indicadores}) == len(indicadores)


def test_indicador_por_unidade_traz_total_participacao_e_posicao():
    unidade = _por_id(indicadores_pca(_linhas()))["unidade-secretaria-de-saude"]

    assert unidade.metadados["valor_total"] == pytest.approx(6000.00)
    assert unidade.metadados["posicao"] == 1
    assert "R$ 6.000,00" in unidade.texto
    assert "60,0% do plano" in unidade.texto


def test_indicador_por_mes_traz_nome_do_mes():
    meses = {i.id: i for i in indicadores_pca(_linhas()) if i.dimensao == "mes"}

    assert set(meses) == {"mes-2025-03", "mes-2025-12"}
    assert "março de 2025" in meses["mes-2025-03"].texto
    assert meses["mes-2025-03"].metadados["valor_total"] == pytest.approx(7000.50)


def test_ano_vem_da_data_desejada_quando_nao_informado():
    assert indicadores_pca(_linhas())[0].metadados["ano"] == "2025"
    assert indicadores_pca(_linhas(), ano=2026)[0].metadados["ano"] == "2026"


def test_ids_nao_colidem_para_nomes_que_so_diferem_por_acento():
    linhas = _linhas()
    linhas.append({**linhas[0], "Unidade Responsável": "POLICIA MILITAR"})

    unidades = [i.id for i in indicadores_pca(linhas) if i.dimensao == "unidade"]

    assert len(unidades) == len(set(unidades)) == 3


def test_sem_linhas_nao_gera_indicadores():
    assert indicadores_pca([]) == []


# --------------------------------------------------------------------------
# Integração com prepare_documents
# --------------------------------------------------------------------------


def test_prepare_documents_gera_indicadores_para_csv_da_base_agregada(tmp_path):
    entrada = tmp_path / "pca"
    entrada.mkdir()
    (entrada / "pca.csv").write_text(CABECALHO + "\n" + "\n".join(LINHAS) + "\n", encoding="utf-8")
    saida = tmp_path / "documents.jsonl"

    resumo = prepare_documents(entrada, saida, tmp_path / "report.json", MANIFEST, base_id="pca_agregado")

    registros = [json.loads(linha) for linha in saida.read_text(encoding="utf-8").splitlines()]
    assert resumo["falhas"] == 0
    assert len(registros) == len(indicadores_pca(_linhas()))  # um chunk por indicador, não por linha
    assert {r["chunking_strategy"] for r in registros} == {"registro_inteiro"}
    assert {r["natureza"] for r in registros} == {"agregada"}
    assert "Estado do Rio de Janeiro" in registros[0]["text"]
    for registro in registros:
        ChunkMetadata.model_validate(registro)
        assert registro["extras"]["dimensao"]


def test_csv_de_base_nao_agregada_continua_um_chunk_por_linha(tmp_path):
    entrada = tmp_path / "siga"
    entrada.mkdir()
    (entrada / "pca.csv").write_text(CABECALHO + "\n" + "\n".join(LINHAS) + "\n", encoding="utf-8")
    saida = tmp_path / "documents.jsonl"

    prepare_documents(entrada, saida, tmp_path / "report.json", MANIFEST, base_id="licitacoes_siga_csv")

    assert len(saida.read_text(encoding="utf-8").splitlines()) == len(LINHAS)


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("2025", "2025-12-31"),
        (2025, "2025-12-31"),
        ("2025-11-14T00:03:45", "2025-11-14"),
        ("15/03/2025", "2025-03-15"),
        ("2025-03-15", "2025-03-15"),
    ],
)
def test_normalize_date_value_aceita_ano_e_data_hora_iso(valor, esperado):
    assert _normalize_date_value(valor) == esperado


def test_resumos_curtos_para_as_perguntas_mais_comuns():
    resumos = {i.id: i for i in indicadores_pca(_linhas(), orgao="Estado do Rio de Janeiro") if i.dimensao == "resumo"}

    assert set(resumos) == {"valor-total", "maiores-classe", "maiores-unidade"}
    assert "R$ 10.000,50" in resumos["valor-total"].texto
    assert resumos["maiores-classe"].texto.index("SUPRIMENTOS PARA USO MEDICO") < resumos["maiores-classe"].texto.index("INTERNET")
    assert all(len(r.texto) < 1200 for r in resumos.values())
