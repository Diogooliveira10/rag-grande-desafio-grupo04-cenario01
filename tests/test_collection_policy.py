"""Recortes são funções puras: não há rede/modelos nestes testes."""
import pytest
from src.collection_policy import PoliticaColeta, ano_assinatura, periodos, tipo_instrumento, uf_unidade


def contrato(**kwargs):
    return {"numeroControlePNCP": "11111111000100-2-000001/2025", "dataAssinatura": "2025-04-01", "dataPublicacaoPncp": "2026-01-02T10:00:00", "unidadeOrgao": {"ufSigla": "RJ"}, **kwargs}


@pytest.mark.parametrize("data,motivo", [("2024-12-31", "assinatura_fora_recorte"), ("2026-01-01", "assinatura_fora_recorte")])
def test_nao_confunde_publicacao_assinatura(data, motivo):
    assert PoliticaColeta().decidir(contrato(dataAssinatura=data)) == (False, motivo)


def test_inclui_assinatura_2025_publicada_2026_e_nao_ano_identificador():
    assert PoliticaColeta().decidir(contrato(numeroControlePNCP="11111111000100-2-000001/2024"))[0]


@pytest.mark.parametrize("nome,tipo", [("Empenho", "empenho"), ("Contrato (termo inicial)", "contrato_assinado"), ("Carta Contrato", "contrato_assinado"), ("Comodato", "outro_instrumento"), ("", "instrumento_nao_informado")])
def test_tipo_oficial_preservado(nome, tipo):
    assert tipo_instrumento(contrato(tipoContrato={"nome": nome})) == tipo


def test_unidade_de_outro_estado_nao_vira_rj_pelo_cnpj():
    assert PoliticaColeta().decidir(contrato(unidadeOrgao={"ufSigla": "DF"}))[1] == "unidade_fora_recorte"


def test_ata_territorio_pendente_nao_e_incluido():
    ata = {"numeroControlePNCPAta": "x", "anoAta": 2025}
    assert PoliticaColeta().decidir(ata) == (False, "territorio_pendente")
    assert PoliticaColeta().decidir(ata, uf_resolvida="RJ")[0]


def test_compra_ano_identificador_diferente_publicada_2025():
    assert PoliticaColeta().decidir({"numeroControlePNCP": "11111111000100-1-000001/2026", "dataPublicacaoPncp": "2025-01-01", "unidadeOrgao": {"ufSigla": "RJ"}})[0]


def test_fingerprint_muda_com_recorte_e_periodos_limitados():
    assert PoliticaColeta().fingerprint() != PoliticaColeta(ano=2024).fingerprint()
    faixas = periodos("2024-01-01", "2026-09-25")
    assert faixas[0][0].isoformat() == "2024-01-01"
    assert faixas[-1][1].isoformat() == "2026-09-25"
    assert all((b-a).days + 1 <= 365 for a, b in faixas)
    with pytest.raises(ValueError):
        periodos("2026-01-01", "2025-01-01")


def test_alias_uf_provider():
    assert uf_unidade({"unidadeExecutora": {"localidade": {"uf": {"siglaUF": "RJ"}}}}) == "RJ"
    assert ano_assinatura({"anoContrato": 2025}) == ""
