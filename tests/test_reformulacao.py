from __future__ import annotations

import json

import pytest

from src import obs, reformulacao
from src.reformulacao import Reformulacao, TurnoAnterior, parece_continuacao, reformular_pergunta

P43 = (
    "Considerando os mesmos contratos de manutenção de computadores, quais regras do "
    "corpus se aplicam quando a modalidade não aparece nos registros?"
)
HISTORICO = [
    TurnoAnterior(
        pergunta="Quais órgãos do RJ contrataram manutenção de computadores em 2025?",
        resposta="Foram encontrados três contratos em 2025.",
    )
]

P43_COM_CONTEXTO = (
    P43 + " (continuação da pergunta anterior: Quais órgãos do RJ contrataram manutenção de "
    "computadores em 2025?)"
)


def _cliente(pergunta: str, chamadas: list | None = None):
    def cliente(mensagens):
        if chamadas is not None:
            chamadas.append(mensagens)
        return json.dumps({"pergunta": pergunta}, ensure_ascii=False)

    return cliente


# --- filtro determinístico -------------------------------------------------


@pytest.mark.parametrize(
    "pergunta",
    [
        P43,
        "E em 2024?",
        "e quanto ao valor total?",
        "Desses, qual tem o maior valor?",
        "Qual o órgão contratante desse contrato?",
        "Considerando os três contratos encontrados em 2025, qual a modalidade predominante?",
        "Quem é o fornecedor dele?",
        "E os outros órgãos?",
        "Isso vale também para 2023?",
    ],
)
def test_reconhece_pergunta_de_continuacao(pergunta):
    assert parece_continuacao(pergunta) is True


@pytest.mark.parametrize(
    "pergunta",
    [
        "Quais contratos de software foram assinados em 2025?",
        "O contrato está vigente?",
        "É possível consultar contratos de ambulância em 2025?",
        "O que o e-mail sintético da DPRJ diz sobre papel A4?",
        "Quais órgãos contrataram manutenção de computadores em 2025?",
        "",
    ],
)
def test_pergunta_que_se_sustenta_sozinha_nao_e_continuacao(pergunta):
    assert parece_continuacao(pergunta) is False


# --- reformular_pergunta ---------------------------------------------------


def test_sem_historico_nao_chama_o_modelo():
    chamadas = []

    resultado = reformular_pergunta(P43, [], cliente=_cliente("outra", chamadas))

    assert resultado == Reformulacao(P43, False, "sem_historico")
    assert chamadas == []


def test_pergunta_autossuficiente_nao_chama_o_modelo():
    chamadas = []
    pergunta = "Quais contratos de software foram assinados em 2025?"

    resultado = reformular_pergunta(pergunta, HISTORICO, cliente=_cliente("outra", chamadas))

    assert resultado == Reformulacao(pergunta, False, "autossuficiente")
    assert chamadas == []


def test_continuacao_e_reescrita_com_o_historico():
    chamadas = []
    reescrita = (
        "Quais regras do corpus se aplicam aos contratos de manutenção de computadores de 2025 "
        "quando a modalidade não aparece nos registros?"
    )

    resultado = reformular_pergunta(P43, HISTORICO, cliente=_cliente(reescrita, chamadas))

    assert resultado == Reformulacao(reescrita, True, "reescrita")
    usuario = chamadas[0][1]["content"]
    assert "manutenção de computadores em 2025" in usuario
    assert "três contratos" in usuario
    assert P43 in usuario
    assert "ignore" in chamadas[0][0]["content"].lower()


def test_so_as_tres_trocas_mais_recentes_entram_no_prompt():
    chamadas = []
    historico = [TurnoAnterior(pergunta=f"pergunta antiga número {n}") for n in range(5)]

    reformular_pergunta("E em 2024?", historico, cliente=_cliente("E em 2024?", chamadas))

    usuario = chamadas[0][1]["content"]
    assert "número 0" not in usuario and "número 1" not in usuario
    assert all(f"número {n}" in usuario for n in (2, 3, 4))


def test_dado_pessoal_do_historico_e_da_pergunta_sai_mascarado_do_prompt():
    chamadas = []
    historico = [
        TurnoAnterior(
            pergunta="O fornecedor com CPF 123.456.789-09 tem contrato?",
            resposta="Fale com joao.silva@gmail.com ou (21) 98888-7777.",
        )
    ]

    reformular_pergunta(
        "E o CPF 987.654.321-00, também?", historico, cliente=_cliente("x", chamadas)
    )

    usuario = chamadas[0][1]["content"]
    for dado in ("123.456.789-09", "987.654.321-00", "joao.silva@gmail.com", "98888-7777"):
        assert dado not in usuario


def test_falha_do_modelo_anexa_a_pergunta_anterior():
    def cliente(mensagens):
        raise ConnectionError("provedor fora do ar")

    resultado = reformular_pergunta(P43, HISTORICO, cliente=cliente)

    assert resultado == Reformulacao(P43_COM_CONTEXTO, True, "llm_falhou")


@pytest.mark.parametrize(
    "saida",
    [
        "isso não é json",
        json.dumps({"outra_chave": "x"}),
        json.dumps({"pergunta": ""}),
        json.dumps({"pergunta": 42}),
        json.dumps({"pergunta": "x" * (reformulacao.MAX_CHARS_REFORMULADA + 1)}),
    ],
)
def test_saida_fora_do_formato_anexa_a_pergunta_anterior(saida):
    resultado = reformular_pergunta(P43, HISTORICO, cliente=lambda mensagens: saida)

    assert resultado == Reformulacao(P43_COM_CONTEXTO, True, "saida_invalida")


def test_falha_volta_ate_a_ultima_pergunta_que_se_sustenta_sozinha():
    historico = [
        TurnoAnterior(pergunta="Quais contratos de software foram assinados em 2025?"),
        TurnoAnterior(pergunta="Quantos contratos de ambulância foram firmados em 2024?"),
        TurnoAnterior(pergunta="E qual foi o valor total deles?"),
    ]

    def cliente(mensagens):
        raise ConnectionError("provedor fora do ar")

    resultado = reformular_pergunta("E desses, qual o maior?", historico, cliente=cliente)

    assert resultado.pergunta == (
        "E desses, qual o maior? (continuação da pergunta anterior: Quantos contratos de "
        "ambulância foram firmados em 2024? / E qual foi o valor total deles?)"
    )


def test_falha_com_ano_na_pergunta_tira_o_ano_da_anterior():
    def cliente(mensagens):
        raise ConnectionError("provedor fora do ar")

    resultado = reformular_pergunta("E em 2024?", HISTORICO, cliente=cliente)

    assert resultado.pergunta == (
        "E em 2024? (continuação da pergunta anterior: Quais órgãos do RJ contrataram "
        "manutenção de computadores?)"
    )


def test_modelo_que_devolve_a_mesma_pergunta_nao_conta_como_reescrita():
    resultado = reformular_pergunta("E em 2024?", HISTORICO, cliente=_cliente("e em 2024?"))

    assert resultado == Reformulacao("E em 2024?", False, "autossuficiente")


def test_modo_desligado_nao_chama_o_modelo(monkeypatch):
    monkeypatch.setattr(reformulacao, "REFORMULACAO_MODE", "regras")
    chamadas = []

    resultado = reformular_pergunta(P43, HISTORICO, cliente=_cliente("outra", chamadas))

    assert resultado == Reformulacao(P43, False, "modo_desligado")
    assert chamadas == []


def test_aceita_historico_em_dict():
    chamadas = []

    reformular_pergunta(
        "E em 2024?", [{"pergunta": "Contratos de TI em 2025?", "resposta": "Há 3."}],
        cliente=_cliente("Contratos de TI em 2024?", chamadas),
    )

    assert "Contratos de TI em 2025?" in chamadas[0][1]["content"]


def test_reformulacao_fica_registrada_no_log():
    reescrita = "Quais regras valem para os contratos de manutenção de computadores de 2025?"

    reformular_pergunta(
        P43, HISTORICO, run_id="r-ref", query_id="q-ref", cliente=_cliente(reescrita)
    )

    arquivo = next(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))
    registros = [json.loads(linha) for linha in arquivo.read_text(encoding="utf-8").splitlines()]
    registro = next(r for r in registros if r["stage"] == "reformulacao")
    assert registro["query_id"] == "q-ref"
    assert registro["output"]["reescrita"] is True
    assert registro["output"]["pergunta"] == reescrita
    assert registro["extra"]["turnos_no_prompt"] == 1
