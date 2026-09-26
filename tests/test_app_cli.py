import json

import pytest

from app import cli
from src.schema import Natureza, RouteDecision


@pytest.fixture
def rota_fake(monkeypatch):
    chamadas = []

    def rotear(pergunta, **kwargs):
        chamadas.append((pergunta, kwargs))
        return RouteDecision(bases_selecionadas=[Natureza.NORMATIVA], justificativa="teste", confidence=0.8)

    monkeypatch.setattr(cli, "rotear_pergunta", rotear)
    return chamadas


@pytest.fixture
def responder_fake(monkeypatch):
    chamadas = []

    def responder(pergunta, **kwargs):
        chamadas.append(pergunta)
        return {"query": pergunta, "answer": "resposta", "is_refusal": False}

    monkeypatch.setattr(cli, "responder", responder)
    return chamadas


def _jsons(saida: str) -> list[dict]:
    """Extrai os objetos JSON impressos após cada linha 'Pergunta: ...'."""
    blocos = [b for b in saida.split("Pergunta: ") if b.strip()]
    return [json.loads(b.split("\n", 1)[1]) for b in blocos]


def test_rota_imprime_decisao_do_roteador(rota_fake, capsys):
    assert cli.main(["rota", "Posso participar de pregão?"]) == 0

    saida = capsys.readouterr().out
    assert "Pergunta: Posso participar de pregão?" in saida
    assert _jsons(saida) == [{"bases_selecionadas": ["normativa"], "justificativa": "teste", "confidence": 0.8}]


def test_rota_passa_run_id_e_query_id_para_o_log(rota_fake):
    cli.main(["rota", "p1", "p2"])

    ids = [kwargs for _, kwargs in rota_fake]
    assert all(k["run_id"] and k["query_id"].startswith("q_") for k in ids)
    assert ids[0]["run_id"] != ids[1]["run_id"]


def test_perguntar_imprime_resposta_do_pipeline(responder_fake, capsys):
    assert cli.main(["perguntar", "Quais contratos?"]) == 0

    assert responder_fake == ["Quais contratos?"]
    assert _jsons(capsys.readouterr().out) == [{"query": "Quais contratos?", "answer": "resposta", "is_refusal": False}]


def test_perguntar_aceita_varias_perguntas_e_ignora_vazias(responder_fake):
    cli.main(["perguntar", "p1", "   ", "p2"])
    assert responder_fake == ["p1", "p2"]


def test_sem_argumento_pede_pergunta_no_terminal(responder_fake, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "pergunta digitada")
    cli.main(["perguntar"])
    assert responder_fake == ["pergunta digitada"]


def test_erro_no_pipeline_vira_codigo_1_e_mensagem_no_stderr(monkeypatch, capsys):
    def quebra(pergunta, **kwargs):
        raise RuntimeError("índice não encontrado")

    monkeypatch.setattr(cli, "responder", quebra)

    assert cli.main(["perguntar", "p"]) == 1
    assert "Erro: índice não encontrado" in capsys.readouterr().err


def test_subcomando_obrigatorio():
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2


def test_rota_real_sem_mock_recusa_juizo_de_legalidade(capsys):
    """Integração offline: o roteador real não chama índice nem LLM."""
    cli.main(["rota", "Esse contrato foi fraude?"])
    assert _jsons(capsys.readouterr().out)[0]["bases_selecionadas"] == []


def test_perguntar_consulta_so_bases_oficiais_por_padrao(monkeypatch):
    escopos = []
    monkeypatch.setattr(cli, "responder", lambda pergunta, **kwargs: escopos.append(kwargs["escopos"]) or {})

    cli.main(["perguntar", "p1"])
    cli.main(["perguntar", "--incluir-experimentais", "p2"])

    assert escopos == [("oficial",), None]

