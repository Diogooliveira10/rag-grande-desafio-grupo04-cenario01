"""Perguntas do golden set que continuam a anterior (item 6 das pendências de 14/10).

P1.3, P4.2 e P4.3 falam de "esses contratos" e "os mesmos contratos": o
avaliador manda as perguntas anteriores da persona como ``historico``, como o
frontend faz numa conversa.
"""

import json
from pathlib import Path

import pytest

from eval import adapters
from eval.run_eval import load_golden_set, render_report_markdown, run_eval

GOLDEN_SET = Path(__file__).resolve().parents[1] / "eval" / "golden_set.jsonl"


def _casos():
    return {caso.id: caso for caso in load_golden_set(GOLDEN_SET)}


def _linha(caso_id, **extra):
    return json.dumps({
        "id": caso_id, "persona": "p", "titulo": "t", "pergunta": f"pergunta {caso_id}", "tipo": "factual",
        "bases_esperadas": ["estruturada"], "is_refusal_esperado": False, "refusal_reason_esperado": None,
        "e_pergunta_de_falha_esperada": False, **extra,
    }) + "\n"


@pytest.mark.parametrize("caso_id,anteriores", [
    ("P1.3", ["P1.1", "P1.2"]), ("P4.2", ["P4.1"]), ("P4.3", ["P4.1", "P4.2"])])
def test_pergunta_dependente_leva_as_perguntas_anteriores_da_persona(caso_id, anteriores):
    casos = _casos()
    assert casos[caso_id].depende_de == anteriores
    assert casos[caso_id].historico == [{"pergunta": casos[anterior].pergunta} for anterior in anteriores]


def test_so_as_perguntas_dependentes_tem_historico():
    assert {caso.id for caso in load_golden_set(GOLDEN_SET) if caso.historico} == {"P1.3", "P4.2", "P4.3"}


@pytest.mark.parametrize("depende_de", [["X9"], ["B"]])
def test_dependencia_inexistente_ou_posterior_falha_no_carregamento(tmp_path, depende_de):
    arquivo = tmp_path / "golden.jsonl"
    arquivo.write_text(_linha("A", depende_de=depende_de) + _linha("B"), encoding="utf-8")
    with pytest.raises(ValueError, match="depende de caso inexistente ou posterior"):
        load_golden_set(arquivo)


def test_adaptador_que_aceita_historico_recebe_as_perguntas_anteriores():
    casos = _casos()
    chamadas = []

    def pipeline(pergunta, historico=None):
        chamadas.append((pergunta, historico))
        return {"bases_consultadas": ["estruturada"], "is_refusal": False,
                "pergunta_reformulada": f"{pergunta} (reescrita)"}

    report = run_eval([casos["P4.1"], casos["P4.2"]], pipeline)

    assert chamadas == [(casos["P4.1"].pergunta, None),
                        (casos["P4.2"].pergunta, [{"pergunta": casos["P4.1"].pergunta}])]
    p42 = report["resultados"][1]
    assert p42["depende_de"] == ["P4.1"] and p42["historico_enviado"] is True
    assert p42["pergunta_reformulada"] == f"{casos['P4.2'].pergunta} (reescrita)"
    assert "depende_de" not in report["resultados"][0]


def test_adaptador_sem_historico_continua_recebendo_so_a_pergunta():
    casos = _casos()
    chamadas = []

    def so_rota(pergunta):
        chamadas.append(pergunta)
        return {"bases_consultadas": ["estruturada"], "is_refusal": False}

    report = run_eval([casos["P4.2"]], so_rota)

    assert chamadas == [casos["P4.2"].pergunta]
    assert report["resultados"][0]["historico_enviado"] is False


def test_relatorio_mostra_o_historico_e_a_pergunta_reescrita():
    casos = _casos()

    def pipeline(pergunta, historico=None):
        return {"bases_consultadas": ["estruturada"], "is_refusal": False, "answer": "resposta",
                "sources_used": [], "pergunta_reformulada": "pergunta reescrita com o contexto"}

    markdown = render_report_markdown(run_eval([casos["P4.3"]], pipeline), "r", "t")

    assert "Histórico: P4.1, P4.2 (enviado)" in markdown
    assert "Pergunta reescrita: pergunta reescrita com o contexto" in markdown


def test_pipeline_completo_repassa_o_historico_ao_responder(monkeypatch):
    recebido = {}

    def responder_falso(pergunta, *, run_id=None, historico=None):
        recebido.update(pergunta=pergunta, historico=historico)
        return {"answer": "ok"}

    monkeypatch.setattr("src.pipeline.responder", responder_falso)
    historico = [{"pergunta": "Quais órgãos contrataram manutenção de computadores em 2025?"}]

    adapters.pipeline_completo("E as modalidades?", historico=historico)

    assert recebido == {"pergunta": "E as modalidades?", "historico": historico}


# Resposta da P4.2 na v7 (gabarito refeito em 2026-10-20): 12 contratos, com
# pregão eletrônico em 6 e 2 sem modalidade. A resposta da v6 ("nenhum dos
# registros informa a modalidade") passou a ser errada.
_RESPOSTA_P42 = (
    "Nos contratos de manutenção de computadores de 2025, o pregão eletrônico aparece em 6 registros, "
    "a dispensa em 3 e a inexigibilidade em 1; 2 registros estão sem modalidade informada."
)


@pytest.mark.parametrize("resposta,aprovada", [
    (_RESPOSTA_P42, True),
    ("Nenhum dos registros informa a modalidade de licitação; não há informações suficientes.", False),
    ("Os registros mostram pregão e dispensa, e a dispensa foi a modalidade predominante.", False),
])
def test_gabarito_da_p42_aceita_a_parafrase_certa_e_reprova_as_erradas(monkeypatch, resposta, aprovada):
    from eval.run_eval import _content_ok

    monkeypatch.setattr("eval.run_eval._probabilidade_de_afirmacao", lambda termo, texto: None)
    resposta = {"bases_consultadas": ["estruturada"], "is_refusal": False, "confidence_level": "baixa",
                "answer": resposta, "sources_used": [{"chunk_id": "contratos_estruturado:c1"}]}
    assert _content_ok(_casos()["P4.2"], resposta) is aprovada


@pytest.mark.parametrize("caso_id", ["I3", "I4"])
def test_casos_de_cnpj_usam_cnpj_valido_e_a_base_estruturada(caso_id):
    from src.query_analysis import analisar_query
    from src.structured_db import cnpj_valido

    caso = _casos()[caso_id]
    cnpj = analisar_query(caso.pergunta).filtros.filtros_analiticos.get("cnpj")
    assert cnpj and cnpj_valido(cnpj)
    assert caso.bases_esperadas == ["estruturada"] and caso.fontes_esperadas_prefixo == ["sql:contratacoes"]
