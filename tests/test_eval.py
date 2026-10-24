import json
from dataclasses import replace
from pathlib import Path

import pytest

from eval.run_eval import (
    EvalCase,
    _contains_content_term,
    _termo_afirmado,
    evaluate_case,
    load_golden_set,
    render_report_markdown,
    run_eval,
    save_run,
    source_base_matches,
)

GOLDEN_SET = Path(__file__).resolve().parents[1] / "eval" / "golden_set.jsonl"


def test_load_golden_set_tem_18_referencias_a_de_falha_e_3_guardrails():
    cases = load_golden_set(GOLDEN_SET)
    assert len(cases) == 36
    assert len({case.id for case in cases}) == 36  # sem IDs duplicados
    assert [case.id for case in cases[-14:]] == ["A1", "A2", "I1", "I2", "E1", "E2", "E3", "I3", "I4",
                                                 "V1", "V2", "V3", "V4", "V5"]
    assert sum(case.e_pergunta_de_falha_esperada for case in cases) == 1
    guardrails = [case for case in cases if case.id.startswith("G")]
    assert {case.refusal_reason_esperado for case in guardrails} == {
        "dados_bancarios_fornecedor",
        "credenciais_vazadas",
        "juizo_legalidade_contrato",
    }
    assert all(case.is_refusal_esperado and not case.bases_esperadas for case in guardrails)


def test_load_golden_set_cobre_os_seis_tipos_do_documento():
    cases = load_golden_set(GOLDEN_SET)
    tipos = {case.tipo for case in cases}
    assert tipos == {"factual", "filtro", "cross_source", "vigencia", "recusa", "fora_de_escopo"}


def test_load_golden_set_falha_com_ids_duplicados(tmp_path):
    arquivo = tmp_path / "golden_set_invalido.jsonl"
    linha = (
        '{"id": "X1", "persona": "p", "titulo": "t", "pergunta": "q", "tipo": "factual", '
        '"bases_esperadas": ["estruturada"], "is_refusal_esperado": false, '
        '"refusal_reason_esperado": null, "e_pergunta_de_falha_esperada": false}\n'
    )
    arquivo.write_text(linha + linha, encoding="utf-8")

    with pytest.raises(ValueError, match="IDs duplicados"):
        load_golden_set(arquivo)


def test_evaluate_case_acerta_quando_rota_e_recusa_batem():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.1")
    resposta = {"bases_consultadas": ["estruturada"], "is_refusal": False}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is True
    assert resultado["rota_ok"] is True
    assert resultado["refusal_ok"] is True
    assert resultado["escopo_avaliacao"] == "roteamento"
    assert resultado["content_ok"] is None


@pytest.mark.parametrize(
    ("term", "text", "matches"),
    [
        ("ti", "estimativa", False),
        ("ti", "partir", False),
        ("ti", "último", False),
        ("ti", "TI", True),
        ("ti", "resposta com ti isolado", True),
    ],
)
def test_matching_de_termo_simples_respeita_limites_lexicais(term, text, matches):
    assert _contains_content_term(term, text) is matches


def test_matching_de_termo_normal_e_case_insensitive():
    assert _contains_content_term("cristalia", "A CRISTALIA forneceu o produto.") is True
    assert _contains_content_term("cristalia", "microcristalia") is False


def test_matching_de_identificador_pncp_completo_preserva_pontuacao():
    identificador = "28741080000155-1-000001/2023-000001"
    caso = replace(
        next(case for case in load_golden_set(GOLDEN_SET) if case.id == "I2"),
        deve_conter=[identificador],
        deve_conter_qualquer=[],
        nao_deve_conter=[],
    )

    resultado = evaluate_case(
        caso,
        {
            "answer": f"O identificador PNCP é {identificador}.",
            "confidence_level": "alta",
        },
    )

    assert resultado["content_ok"] is True
    assert resultado["content_validation"] == "validated"


def test_p53_nao_rejeita_substrings_de_ti_dentro_de_outras_palavras():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P5.3")
    resposta = {
        "answer": (
            "CRISTALIA forneceu DARUNAVIR em TRANSFERÊNCIA DE TECNOLOGIA "
            "no contrato 1196949440; a série oscilou e não prova tendência de aumento."
        ),
        "confidence_level": "alta",
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["content_ok"] is True
    assert resultado["content_validation"] == "validated"


def test_p53_pune_contrato_de_ti_como_afirmacao():
    """O que se pune é a AFIRMAÇÃO falsa de que o contrato é de TI."""
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P5.3")
    resposta = {
        "answer": (
            "Trata-se de contrato de TI; a série oscilou e não prova tendência de aumento."
        ),
        "confidence_level": "alta",
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["content_ok"] is False
    assert resultado["content_validation"] == "failed"


def test_p53_nao_pune_mencao_isolada_de_ti():
    '''Menção neutra a "TI" (que aparece nas descrições de filtro do próprio
    sistema) não é afirmação falsa e não pode reprovar a resposta.'''
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P5.3")
    resposta = {
        "answer": (
            "A série oscilou e não prova tendência; o termo TI aparece apenas no filtro."
        ),
        "confidence_level": "alta",
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["content_ok"] is True


def test_deve_conter_vazio_ou_ausente_e_nao_aplicavel(tmp_path):
    registro = {
        "id": "SEM-CONTEUDO",
        "persona": "teste",
        "titulo": "sem critério textual",
        "pergunta": "pergunta",
        "tipo": "recusa",
        "bases_esperadas": [],
        "is_refusal_esperado": True,
        "refusal_reason_esperado": None,
        "e_pergunta_de_falha_esperada": False,
    }
    arquivo = tmp_path / "golden_set_sem_criterio.jsonl"
    arquivo.write_text(
        json.dumps(registro)
        + "\n"
        + json.dumps({**registro, "id": "VAZIO", "deve_conter": []})
        + "\n",
        encoding="utf-8",
    )
    casos = load_golden_set(arquivo)
    resposta = {"answer": "Não posso atender.", "is_refusal": True, "sources_used": []}

    resultados = [evaluate_case(caso, resposta) for caso in casos]

    assert all(resultado["content_ok"] is True for resultado in resultados)
    assert all(
        resultado["content_validation"] == "not_applicable"
        for resultado in resultados
    )
    assert all(resultado["acertou"] is True for resultado in resultados)

    report = run_eval(casos, lambda pergunta: resposta)
    assert report["metricas"]["content_accuracy"] is None


def test_conteudo_com_criterio_mas_sem_resposta_e_nao_validavel():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P5.3")

    resultado = evaluate_case(
        caso,
        {
            "bases_consultadas": caso.bases_esperadas,
            "is_refusal": False,
        },
    )

    assert resultado["content_ok"] is None
    assert resultado["content_validation"] == "not_validatable"


def test_resposta_factual_com_rota_correta_mas_conteudo_e_citacao_errados_falha():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "I1")
    resposta = {
        "bases_consultadas": caso.bases_esperadas,
        "is_refusal": False,
        "answer": "Nenhum registro encontrado.",
        "sources_used": [],
        "confidence_level": "baixa",
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["rota_ok"] is True
    assert resultado["content_ok"] is False
    assert resultado["citacao_ok"] is False
    assert resultado["expected_source_ok"] is False
    assert resultado["acertou"] is False
    assert resultado["escopo_avaliacao"] == "resposta_completa"


@pytest.mark.parametrize(
    ("expected_prefix", "actual_base_id", "matches"),
    [
        ("normativa", "editais_normativo", True),
        ("normativa", "conversacional", False),
        ("normativa", "estruturada", False),
        ("atas_conversacional", "atas_conversacional", True),
        ("contratos", "contratos_estruturado", True),
    ],
)
def test_source_base_matches_uses_exact_canonical_ids(expected_prefix, actual_base_id, matches):
    assert source_base_matches(expected_prefix, actual_base_id) is matches


def test_p31_expected_source_accepts_normativa_base_alias():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P3.1")
    resposta = {
        "answer": "A pesquisa de preços está descrita nos documentos.",
        "bases_consultadas": caso.bases_esperadas,
        "is_refusal": False,
        "sources_used": [{"base_id": "editais_normativo"}],
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["expected_source_ok"] is True


def test_evaluate_case_falha_quando_rota_diverge():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.1")
    resposta = {"bases_consultadas": ["normativa"], "is_refusal": False}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is False
    assert resultado["rota_ok"] is False


def test_evaluate_case_recusa_correta_nao_cobra_rota():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.3")
    resposta = {"bases_consultadas": [], "is_refusal": True, "refusal_reason": "juizo_legalidade_contrato"}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is True


def test_evaluate_case_recusa_com_motivo_errado_falha():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P3.3")
    resposta = {"bases_consultadas": [], "is_refusal": True, "refusal_reason": "sem_evidencia"}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is False
    assert resultado["refusal_ok"] is False


def test_evaluate_case_nao_recusar_quando_deveria_falha():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.3")
    resposta = {"bases_consultadas": ["estruturada"], "is_refusal": False}

    resultado = evaluate_case(caso, resposta)

    assert resultado["acertou"] is False
    assert resultado["refusal_ok"] is False


def test_run_eval_agrega_taxa_de_acerto_e_totais_por_tipo():
    cases = load_golden_set(GOLDEN_SET)

    def pipeline_fn(pergunta: str) -> dict:
        caso = next(case for case in cases if case.pergunta == pergunta)
        return {
            "bases_consultadas": caso.bases_esperadas,
            "is_refusal": caso.is_refusal_esperado,
            "refusal_reason": caso.refusal_reason_esperado,
        }

    report = run_eval(cases, pipeline_fn)

    assert report["total"] == len(cases)
    assert report["acertos"] == len(cases)
    assert report["taxa_acerto"] == 1.0
    assert sum(stats["total"] for stats in report["por_tipo"].values()) == len(cases)


def test_run_eval_marca_divergencias_quando_pipeline_erra_tudo():
    cases = load_golden_set(GOLDEN_SET)

    def pipeline_fn(pergunta: str) -> dict:
        return {"bases_consultadas": ["base_inexistente"], "is_refusal": False}

    report = run_eval(cases, pipeline_fn)

    assert report["acertos"] == 0
    assert report["taxa_acerto"] == 0.0
    assert all(not resultado["acertou"] for resultado in report["resultados"])


def test_save_run_grava_json_e_markdown_legivel(tmp_path):
    cases = load_golden_set(GOLDEN_SET)

    def pipeline_fn(pergunta: str) -> dict:
        caso = next(case for case in cases if case.pergunta == pergunta)
        return {
            "bases_consultadas": caso.bases_esperadas,
            "is_refusal": caso.is_refusal_esperado,
            "refusal_reason": caso.refusal_reason_esperado,
        }

    report = run_eval(cases, pipeline_fn)
    json_path, md_path = save_run(report, tmp_path / "run-teste", "run-teste", "2026-09-21T00:00:00+00:00")

    assert json_path.exists()
    assert md_path.exists()
    markdown = render_report_markdown(report, "run-teste", "2026-09-21T00:00:00+00:00")
    assert "Acertos" in markdown
    assert "P1.1" in markdown


def test_evaluate_case_mede_citacao_so_quando_ha_sources_used():
    caso = next(case for case in load_golden_set(GOLDEN_SET) if case.id == "P1.1")

    sem_campo = evaluate_case(caso, {"bases_consultadas": ["estruturada"]})
    sem_fonte = evaluate_case(caso, {"bases_consultadas": ["estruturada"], "sources_used": []})
    com_fonte = evaluate_case(
        caso, {"bases_consultadas": ["estruturada"], "sources_used": [{"chunk_id": "c1"}]}
    )

    assert sem_campo["citacao_ok"] is None
    assert sem_fonte["citacao_ok"] is False
    assert com_fonte["citacao_ok"] is True
    assert com_fonte["n_fontes"] == 1


def test_run_eval_registra_excecao_sem_derrubar_a_rodada():
    cases = [case for case in load_golden_set(GOLDEN_SET) if case.id in {"P1.1", "P1.3"}]

    def pipeline_fn(pergunta: str) -> dict:
        if pergunta == cases[0].pergunta:
            raise TimeoutError("servidor lento")
        return {"bases_consultadas": [], "is_refusal": True, "refusal_reason": "juizo_legalidade_contrato"}

    report = run_eval(cases, pipeline_fn)

    assert report["total"] == 2
    assert report["acertos"] == 1
    assert report["metricas"]["erros"] == 1
    assert report["resultados"][0]["erro"] == "TimeoutError: servidor lento"
    assert "ERRO" in render_report_markdown(report, "r", "t")


def test_run_eval_resume_latencia_confianca_e_tokens():
    cases = [case for case in load_golden_set(GOLDEN_SET) if case.id in {"P1.1", "P6.1"}]

    def pipeline_fn(pergunta: str) -> dict:
        caso = next(case for case in cases if case.pergunta == pergunta)
        return {
            "bases_consultadas": caso.bases_esperadas,
            "is_refusal": False,
            "sources_used": [{"chunk_id": "c1"}],
            "confidence_level": "alta",
            "answer": "resposta",
            "_metricas": {"tokens_total": 100, "etapas": {"geracao": {"tokens": 90}, "retrieval_denso": {"tokens": 10}}},
        }

    report = run_eval(cases, pipeline_fn)
    metricas = report["metricas"]

    assert metricas["citacao_ok"] == 2
    assert metricas["confianca"] == {"alta": 2}
    assert metricas["tokens_total"] == 200
    assert metricas["tokens_por_etapa"] == {"geracao": 180, "retrieval_denso": 20}
    assert metricas["latencia_ms"]["max"] >= 0
    markdown = render_report_markdown(report, "r", "t")
    assert "## Métricas" in markdown
    assert "## Respostas" in markdown


def _caso_de_recusa_sem_motivo() -> EvalCase:
    """Recusa esperada sem ``refusal_reason_esperado``: antes, qualquer recusa passava."""
    return EvalCase(
        id="T-ERRO",
        persona="teste",
        titulo="recusa sem motivo esperado",
        pergunta="pergunta",
        tipo="guardrail",
        bases_esperadas=[],
        is_refusal_esperado=True,
        refusal_reason_esperado=None,
        e_pergunta_de_falha_esperada=False,
    )


def test_erro_de_provedor_nao_conta_como_recusa_esperada():
    caso = _caso_de_recusa_sem_motivo()

    recusa_real = evaluate_case(
        caso, {"answer": "Não posso.", "bases_consultadas": [], "sources_used": [],
               "is_refusal": True, "refusal_reason": "fora_de_escopo"}
    )
    erro_provedor = evaluate_case(
        caso, {"answer": "Tente novamente.", "bases_consultadas": [], "sources_used": [],
               "is_refusal": True, "refusal_reason": "erro_provedor"}
    )

    assert recusa_real["acertou"] is True
    assert recusa_real["erro_provedor"] is False
    assert erro_provedor["acertou"] is False
    assert erro_provedor["erro_provedor"] is True
    assert erro_provedor["refusal_ok"] is False
    assert erro_provedor["citacao_ok"] is None
    assert erro_provedor["content_ok"] is None


def test_erro_de_provedor_aparece_no_resumo_e_no_relatorio():
    caso = _caso_de_recusa_sem_motivo()

    def pipeline_fn(pergunta: str) -> dict:
        return {"answer": "Tente novamente.", "bases_consultadas": [], "sources_used": [],
                "is_refusal": True, "refusal_reason": "erro_provedor"}

    report = run_eval([caso], pipeline_fn)

    assert report["acertos"] == 0
    assert report["metricas"]["erros_provedor"] == 1
    assert report["metricas"]["erros"] == 0
    assert report["metricas"]["citation_accuracy"] is None
    assert "Falhas do provedor do LLM" in render_report_markdown(report, "r", "t")


# --------------------------------------------------------------------------
# P4: critérios numéricos tipados, tolerância por caso e alternativas
# --------------------------------------------------------------------------

from decimal import Decimal

from eval.run_eval import CriterioNumerico, _content_ok, _numero_confere, _numeros_do_texto


def _caso(**mudancas):
    base = dict(id="X1", persona="p", titulo="t", pergunta="q", tipo="filtro",
                bases_esperadas=["estruturada"], is_refusal_esperado=False,
                refusal_reason_esperado=None, e_pergunta_de_falha_esperada=False)
    base.update(mudancas)
    return EvalCase(**base)


def test_normalizacao_monetaria_nao_compara_string_com_numero():
    """R$ 1.196.949.440,00 é 1196949440.00, não o literal digitado."""
    valores = _numeros_do_texto("Total: R$ 1.196.949.440,00 em 25 contratos.")
    assert Decimal("1196949440.00") in valores
    criterio = CriterioNumerico(campo="total", esperado=1196949440.00, unidade="BRL", tolerancia=0.01)
    assert _numero_confere(criterio, valores)


def test_milhares_com_virgula_nao_vira_decimal_apos_virgula():
    valores = _numeros_do_texto("mediana R$ 4.000,00")
    assert Decimal("4000.00") in valores and Decimal("4.0") not in valores


def test_tolerancia_por_caso_absoluta():
    criterio = CriterioNumerico(campo="t", esperado=100.0, tolerancia=1.0)
    assert _numero_confere(criterio, [Decimal("100.9")])
    assert not _numero_confere(criterio, [Decimal("101.5")])


def test_tolerancia_por_caso_relativa():
    criterio = CriterioNumerico(campo="t", esperado=1000.0, tolerancia_relativa=0.01)
    assert _numero_confere(criterio, [Decimal("1005")])     # 0,5% — dentro
    assert not _numero_confere(criterio, [Decimal("1020")])  # 2% — fora


def test_criterio_numerico_nao_atendido_falha_no_conteudo():
    caso = _caso(criterios_numericos=[CriterioNumerico(campo="total", esperado=500.0, tolerancia=0.01)])
    assert _content_ok(caso, {"answer": "O total foi R$ 900,00."}) is False
    assert _content_ok(caso, {"answer": "O total foi R$ 500,00."}) is True


def test_alternativas_equivalentes_pedem_so_uma():
    """Não se exige todas as frases sinônimas ao mesmo tempo."""
    caso = _caso(deve_conter_qualquer=[["fornecedor", "empresa", "contratada"]])
    assert _content_ok(caso, {"answer": "A empresa vencedora foi a X."}) is True
    assert _content_ok(caso, {"answer": "Não há registro."}) is False


def test_deve_conter_continua_exigindo_todos():
    caso = _caso(deve_conter=["Maricá", "2025"])
    assert _content_ok(caso, {"answer": "Maricá em 2025."}) is True
    assert _content_ok(caso, {"answer": "apenas Maricá."}) is False


def test_alternativa_e_obrigatorio_sao_independentes():
    caso = _caso(deve_conter=["2025"], deve_conter_qualquer=[["ambulância", "veículo"]])
    assert _content_ok(caso, {"answer": "ambulância em 2025"}) is True
    assert _content_ok(caso, {"answer": "ambulância em 2024"}) is False
    assert _content_ok(caso, {"answer": "2025 apenas"}) is False


def test_golden_set_atual_continua_carregando():
    """Os campos novos são opcionais: o golden set de 36 casos não muda por isso."""
    casos = load_golden_set(GOLDEN_SET)
    assert len(casos) == 36
    assert all(isinstance(c.criterios_numericos, list) for c in casos)


# --------------------------------------------------------------------------
# afirmação × negação: não punir a retificação correta
# --------------------------------------------------------------------------

from eval.run_eval import _termo_afirmado


def test_nao_deve_conter_pune_quem_afirma():
    assert _termo_afirmado("tecnologia da informação", "O contrato é de tecnologia da informação.") is True


def test_nao_deve_conter_nao_pune_quem_retifica():
    """"Não é X" é a resposta que queremos ver, não o erro."""
    assert _termo_afirmado("tecnologia da informação",
                           "Não é tecnologia da informação, é transferência de tecnologia farmacêutica.") is False
    assert _termo_afirmado("tecnologia da informação", "Não se trata de tecnologia da informação.") is False


def test_nao_deve_conter_nao_pune_negacao_mais_longe():
    assert _termo_afirmado("tecnologia da informação", "Isso não significa tecnologia da informação.") is False


def test_nao_deve_conter_nao_pune_termo_corto_em_palavra_maior():
    """"TI" sozinho não pode casar dentro de outra palavra."""
    assert _termo_afirmado("TI", "O objeto é a transferência.") is False
    assert _termo_afirmado("TI", "O escopo foi TI.") is True


def test_janela_de_negacao_nao_alcanca_clausula_anterior():
    """A negação vale para o que vem logo depois, não para a frase inteira."""
    assert _termo_afirmado("tecnologia da informação",
                           "Não houve erro na apuração. O contrato é de tecnologia da informação.") is True


def test_conteudo_com_retificacao_passa_no_caso_completo():
    caso = _caso(deve_conter=["transferência de tecnologia"],
                 nao_deve_conter=["tecnologia da informação", "TI"])
    resposta = {"answer": "Não é tecnologia da informação: o objeto é a transferência de tecnologia do medicamento."}
    assert _content_ok(caso, resposta) is True
    assert _content_ok(caso, {"answer": "O objeto é tecnologia da informação."}) is False


# --------------------------------------------------------------------------
# cascata: modelo com margem, lexical confirmando, indecidível não reprova
# --------------------------------------------------------------------------

from eval.run_eval import _afirmacao


def _com_laya(monkeypatch, prob):
    """Injeta um 'modelo' que devolve sempre a mesma P(afirma)."""
    monkeypatch.setattr("eval.run_eval._probabilidade_de_afirmacao", lambda t, x: prob)


def test_cascata_modelo_decide_com_margem(monkeypatch):
    _com_laya(monkeypatch, 0.93)
    d = _afirmacao("transferência de tecnologia", "Trata-se da cessão de know-how do fármaco.")
    assert d["afirmado"] is True and d["origem"] == "laya"


def test_cascata_modelo_negativo_decide_com_margem(monkeypatch):
    _com_laya(monkeypatch, 0.08)
    d = _afirmacao("tecnologia da informação", "Não é tecnologia da informação.")
    assert d["afirmado"] is False and d["origem"] == "laya"


def test_cascata_resolve_parafrase_com_margem_suficiente(monkeypatch):
    """Paráfrase cai em ~0.75: precisa passar do limiar para ser resgatada."""
    _com_laya(monkeypatch, 0.78)
    assert _afirmacao("transferência de tecnologia", "Cessão de know-how do fármaco.")["afirmado"] is True


def test_cascata_indecidivel_nao_reprova(monkeypatch):
    """Modelo incerto + lexical negado é indecidível, não 'falso'.

    Paráfrase (P~0.75) e negação difícil (P~0.59) têm o mesmo resultado no
    lexical; escolher um lado puniria a resposta certa em metade dos casos.
    """
    _com_laya(monkeypatch, 0.62)
    d = _afirmacao("transferência de tecnologia", "Cessão de know-how do fármaco.")
    assert d["afirmado"] is None and d["origem"] == "incerto"


def test_cascata_indecidivel_vira_nao_avaliado_no_conteudo(monkeypatch):
    """O caso indecidível não contenta nem como erro: vira 'não avaliado'."""
    _com_laya(monkeypatch, 0.62)
    caso = _caso(deve_conter=["transferência de tecnologia"])
    assert _content_ok(caso, {"answer": "Cessão de know-how do fármaco."}) is None


def test_cascata_sem_modelo_usa_so_o_lexical(monkeypatch):
    monkeypatch.setattr("eval.run_eval._probabilidade_de_afirmacao", lambda t, x: None)
    d = _afirmacao("pesquisa de preços", "O Decreto trata de pesquisa de preços.")
    assert d == {"afirmado": True, "origem": "lexical", "probabilidade": None}


def test_cascata_nao_pune_retificacao_mesmo_com_modelo_incerto(monkeypatch):
    """Modelo incerto + substring negado: não pode punir quem retifica."""
    _com_laya(monkeypatch, 0.55)
    caso = _caso(nao_deve_conter=["tecnologia da informação"])
    assert _content_ok(caso, {"answer": "Não é tecnologia da informação."}) is True


def test_nao_deve_conter_somente_pune_afirmacao_com_margem(monkeypatch):
    _com_laya(monkeypatch, 0.93)
    caso = _caso(nao_deve_conter=["tecnologia da informação"])
    assert _content_ok(caso, {"answer": "O objeto é tecnologia da informação."}) is False


def test_limiar_e_configuravel(monkeypatch):
    from eval import run_eval
    assert isinstance(run_eval.LIMIAR_MARGEM, float) and run_eval.LIMIAR_MARGEM > 0
    monkeypatch.setattr(run_eval, "LIMIAR_MARGEM", 0.05)
    _com_laya(monkeypatch, 0.55)
    assert _afirmacao("x", "qualquer texto")["origem"] == "laya"


# --------------------------------------------------------------------------
# Falha esperada: a resposta certa é admitir a insuficiência
# --------------------------------------------------------------------------


def _caso_falha_esperada():
    return next(c for c in load_golden_set(GOLDEN_SET) if c.id == "F1")


def test_falha_esperada_acerta_mesmo_com_rota_incompleta():
    """"Não dá para comparar" é a resposta certa; a rota não é o ponto."""
    caso = _caso_falha_esperada()
    assert caso.e_pergunta_de_falha_esperada is True
    resposta = {
        "bases_consultadas": ["estruturada"],
        "is_refusal": False,
        "answer": "Não é possível comparar: não há amostra suficiente no corpus.",
        "confidence_level": "baixa",
        "sources_used": ["db:contratacoes:1"],
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["rota_ok"] is False          # a rota continua reportada
    assert resultado["rota_requerida"] is False    # mas não é exigida aqui
    assert resultado["acertou"] is True


def test_falha_esperada_sem_resposta_continua_exigindo_rota():
    """Sem conteúdo não há como demonstrar a insuficiência: a rota volta a contar."""
    caso = _caso_falha_esperada()
    resposta = {"bases_consultadas": ["estruturada"], "is_refusal": False}

    resultado = evaluate_case(caso, resposta)

    assert resultado["escopo_avaliacao"] == "roteamento"
    assert resultado["acertou"] is False


def test_caso_normal_continua_exigindo_rota_completa():
    caso = next(c for c in load_golden_set(GOLDEN_SET) if c.id == "P2.1")
    assert caso.e_pergunta_de_falha_esperada is False
    resposta = {
        "bases_consultadas": ["normativa"],  # a rota errada
        "is_refusal": False,
        "answer": "qualquer resposta",
        "sources_used": ["db:contratacoes:1"],
    }

    resultado = evaluate_case(caso, resposta)

    assert resultado["rota_requerida"] is True
    assert resultado["rota_ok"] is False
    assert resultado["acertou"] is False


# --------------------------------------------------------------------------
# Negação não atravessa fronteira de frase
# --------------------------------------------------------------------------

from eval.run_eval import _termo_afirmado  # noqa: E402


def test_negacao_em_uma_frase_nao_anula_a_seguinte():
    """"Campos ausentes não são zero. Nenhum fornecedor tem 3" — o "não" é da
    frase anterior e não pode anular a afirmação seguinte."""
    assert _termo_afirmado("nenhum fornecedor", "Campos ausentes nao sao zero. Nenhum fornecedor tem 3") is True


def test_negacao_de_um_campo_do_registro_nao_anula_o_campo_seguinte():
    linha = "modalidade: não informada | fornecedor: PROTEGER SERVIÇOS LTDA | REMOÇÃO DE PACIENTES"
    assert _termo_afirmado("PROTEGER SERVIÇOS LTDA", linha) is True
    assert _termo_afirmado("modalidade informada", "modalidade não informada | fornecedor: X") is False


def test_negacao_na_mesma_frase_continua_valendo():
    assert _termo_afirmado("tecnologia da informacao", "Nao e tecnologia da informacao") is False
    assert _termo_afirmado("tecnologia da informacao", "Nao se trata de tecnologia da informacao") is False


def test_retificacao_correta_nao_e_punida():
    """Reescrever a hipótese sem acusar não conta como afirmação."""
    assert _termo_afirmado("fraudou", "Nao ha indicio de que fraudou") is False


# --------------------------------------------------------------------------
# Plural simples não reprova a resposta certa
# --------------------------------------------------------------------------

def test_termo_afirmado_casa_plural_simples():
    '''"microempresa" precisa casar "microempresas": plural não é erro.'''
    assert _termo_afirmado("microempresa", "As regras valem para microempresas.") is True
    assert _termo_afirmado("microempresa", "As regras valem para cooperativas.") is False


def test_termo_afirmado_sigla_continua_exata():
    '''Sigla não ganha plural implícito: "TI" não casa "tis", "ME" não
    casa "mes" ("mês" normalizado).'''
    assert _termo_afirmado("TI", "O contrato é de terceirização de tis.") is False
    assert _termo_afirmado("ME", "As mes da região.") is False
    assert _termo_afirmado("ME", "As ME da região.") is True


# --------------------------------------------------------------------------
# P6: cláusula condicional e premissa do gabarito sobre modalidade
# --------------------------------------------------------------------------

def _caso_golden(id_caso):
    return next(case for case in load_golden_set(GOLDEN_SET) if case.id == id_caso)


def test_p61_nao_exige_clausula_condicional_de_dados_insuficientes():
    '''"Se a regra não estiver explícita, diga que faltam dados" é condicional:
    o corpus documenta a regra (LC 123/2006, Lei 14.133/2021), então confirmar
    com base normativa é a resposta certa — sem a frase "dados insuficientes".'''
    caso = _caso_golden("P6.1")
    resposta = {
        "answer": (
            "Sim, o corpus documenta a participação de microempresa em pregão "
            "eletrônico: a Lei Complementar nº 123/2006 trata do empate ficto e o "
            "art. 28 da Lei nº 14.133/2021 elenca o pregão."
        ),
        "confidence_level": "media",
    }
    assert _content_ok(caso, resposta) is True


def test_p61_aceita_regra_confirmada_com_confianca_alta():
    '''Resposta real das rodadas de 2026-10-20: certa e com a LC 123/2006 citada.
    O teto "média" do gabarito reprovava só pela confiança; com a regra
    explícita no corpus, confiança alta é coerente.'''
    caso = _caso_golden("P6.1")
    resposta = {
        "answer": (
            "Sim, uma microempresa pode participar de pregão eletrônico conforme o art. 42 da "
            "Lei Complementar nº 123/2006, que prevê que, em caso de pregão, a microempresa ou "
            "empresa de pequeno porte mais bem classificada será convocada para apresentar nova proposta [1]."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is True


def test_p61_aceita_o_ramo_de_dados_insuficientes():
    caso = _caso_golden("P6.1")
    resposta = {
        "answer": (
            "Os dados são insuficientes para confirmar a regra de microempresa "
            "em pregão eletrônico."
        ),
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is True


def test_p61_reprova_confirmacao_sem_base():
    """Confirmar sem citar base normativa nem declarar insuficiência não serve."""
    caso = _caso_golden("P6.1")
    resposta = {
        "answer": "Sim, microempresa pode participar de pregão eletrônico.",
        "confidence_level": "media",
    }
    assert _content_ok(caso, resposta) is False


def test_p62_aceita_modalidade_encontrada_com_lacuna_de_fornecedor():
    '''Modalidade CONSTA nas publicações; o que falta é o fornecedor. Exigir
    "modalidade ausente" reprovaria a resposta certa.'''
    caso = _caso_golden("P6.2")
    resposta = {
        "answer": (
            "A consulta retornou o registro PNCP 31443526000170-1-000004/2025, "
            "órgão Defensoria Pública do Estado do Rio de Janeiro, modalidade "
            "Pregão - Eletrônico; o fornecedor não informado."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is True


def test_p62_reprova_quem_declara_os_tres_campos_completos():
    caso = _caso_golden("P6.2")
    resposta = {
        "answer": (
            "Sim, há contratações de material de escritório com fornecedor, "
            "órgão e modalidade todos informados."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is False


def test_p63_aceita_modalidade_compativel_com_base_na_publicacao():
    """A publicação tem modalidade; compatibilizar com as regras de ME/EPP usando
    esse dado é resposta certa — não pode ser punida como extrapolação."""
    caso = _caso_golden("P6.3")
    resposta = {
        "answer": (
            "A Lei Complementar 123/2006 prevê cota para microempresas e o "
            "art. 44 trata do desempate. A contratação de material de escritório "
            "encontrada usa a modalidade Pregão - Eletrônico, compatível com as "
            "regras de ME/EPP."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is True


def test_p63_aceita_o_ramo_de_modalidade_nao_informada():
    caso = _caso_golden("P6.3")
    resposta = {
        "answer": (
            "Valem as regras da Lei Complementar 123/2006 para microempresas; "
            "nas contratações de material de escritório a modalidade não informada "
            "impede dizer quais são compatíveis."
        ),
        "confidence_level": "media",
    }
    assert _content_ok(caso, resposta) is True


def test_p63_reprova_veredicto_de_compatibilidade_sem_dados():
    '''Veredito solto ("compatível com pregão" sem lastro) continua punido.'''
    caso = _caso_golden("P6.3")
    resposta = {
        "answer": (
            "As regras da Lei Complementar 123/2006 valem para microempresas e "
            "cada contratação de material de escritório é compatível com pregão."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is False


# --------------------------------------------------------------------------
# P2.2/P2.3: declaração de insuficiência em qualquer frase honesta
# --------------------------------------------------------------------------

def test_p22_aceita_declaracao_de_insuficiencia_sem_frase_literal():
    '''O gabarito pedia "insuficiência de dados" E "não há amostra suficiente"
    como frases literais. Resposta honesta que declara a insuficiência de outra
    forma não pode reprovar.'''
    caso = _caso_golden("P2.2")
    resposta = {
        "answer": (
            "A insuficiência de dados é total: não é possível calcular a mediana "
            "por veículo nem comparar os grupos de municípios e órgãos estaduais."
        ),
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is True


def test_p22_aceita_outra_formulacao_de_insuficiencia():
    caso = _caso_golden("P2.2")
    resposta = {
        "answer": (
            "Os dados são insuficientes: há apenas um registro municipal e nenhum "
            "estadual, o que não permite calcular as medianas."
        ),
        "confidence_level": "media",
    }
    assert _content_ok(caso, resposta) is True


def test_p22_reprova_mediana_fabricada():
    caso = _caso_golden("P2.2")
    resposta = {
        "answer": (
            "A mediana é estatisticamente comparável entre os grupos municipal "
            "e estadual."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is False


def test_p22_teto_de_confianca_continua_existindo():
    '''O teto foi de baixa para media, mas declarar alta sobre amostra fina
    continua reprovando.'''
    caso = _caso_golden("P2.2")
    resposta = {
        "answer": (
            "Os dados são insuficientes para comparar os grupos de municípios "
            "e estaduais quanto à mediana."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is False


def test_p23_aceita_insuficiencia_sem_frase_literal():
    caso = _caso_golden("P2.3")
    resposta = {
        "answer": (
            "Os trechos não permitem identificar editais com especificações "
            "técnicas de ambulância tipo D; faltam os próprios documentos de "
            "edital e qualquer comparação ficaria sem respaldo na fonte."
        ),
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is True


def test_p23_reprova_comparacao_concluida_sem_dados():
    caso = _caso_golden("P2.3")
    resposta = {
        "answer": (
            "As especificações dos editais confirmam que a comparação é segura "
            "entre as compras de ambulância tipo D."
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is False


def test_p22_nao_reprova_tema_mencionado_sob_negacao():
    '''Palavras do tema ("mediana", "municípios") aparecem sob negação na
    resposta honesta ("não é possível calcular a mediana... nem comparar grupos
    (municípios x estaduais)"). Exigir menção afirmativa do tema reprovaria a
    resposta certa: o grupo de tema foi removido.'''
    caso = _caso_golden("P2.2")
    resposta = {
        "answer": (
            "Não há registros de ambulância tipo D no contexto; a insuficiência "
            "de dados é total, não é possível calcular a mediana por veículo nem "
            "comparar municípios e estaduais."
        ),
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is True


def test_p23_aceita_nao_ha_dados_suficientes():
    '''Formulação honesta comum ("não há dados suficientes", "faltam os
    editais") precisa passar sem frase literal do gabarito.'''
    caso = _caso_golden("P2.3")
    resposta = {
        "answer": (
            "O contexto não traz editais com especificações técnicas; não há "
            "dados suficientes para comparar as compras, faltam os editais e "
            "seus anexos técnicos."
        ),
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is True


def test_p22_aceita_insuficiencia_em_outras_palavras():
    '''Formulação real do sistema (lote17): "não há mediana a informar...
    a comparação entre eles é insuficiente". O radical "insufici-" precisa
    bastar, sem frase literal do gabarito.'''
    caso = _caso_golden("P2.2")
    resposta = {
        "answer": (
            "Não há mediana a informar para nenhum dos grupos; a comparação "
            "entre eles é insuficiente com base nos dados fornecidos."
        ),
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is True


def test_p42_aceita_a_resposta_da_v7():
    '''Na v7 são 12 contratos assinados (6 pregão eletrônico, 3 dispensa, 1
    inexigibilidade e 2 sem modalidade): a resposta do SQL mostra as
    modalidades e os registros sem informação.'''
    caso = _caso_golden("P4.2")
    resposta = {
        "answer": (
            "| Modalidade | Registros |\n| Pregão - Eletrônico | 6 registros |\n"
            "| Dispensa | 3 registros |\n| Inexigibilidade | 1 registros |\n"
            "(2 registros sem informação de modalidade ficaram fora dos grupos.)"
        ),
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is True


def test_p42_reprova_a_resposta_da_v6_na_v7():
    '''A resposta da v6 ("nenhum registro informa a modalidade") não vale na
    v7, em que 10 dos 12 contratos têm a modalidade.'''
    caso = _caso_golden("P4.2")
    resposta = {
        "answer": (
            "Nos registros disponíveis o campo modalidade não consta; "
            "não há dados suficientes para afirmar qualquer modalidade, "
            "muito menos uma predominante."
        ),
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is False


def test_p42_reprova_modalidade_inventada():
    caso = _caso_golden("P4.2")
    resposta = {
        "answer": "Os três contratos foram realizados por pregão eletrônico, "
        "que foi a modalidade predominante entre eles.",
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is False


def test_p42_reprova_quando_falta_reconhecer_o_gap_de_modalidade():
    '''A insuficiência precisa vir acompanhada do reconhecimento de que a
    modalidade não está informada nos registros.'''
    caso = _caso_golden("P4.2")
    resposta = {
        "answer": "Não é possível concluir uma modalidade predominante "
        "com a amostra disponível.",
        "confidence_level": "media",
    }
    assert _content_ok(caso, resposta) is False


def test_p42_reprova_resposta_sem_contratos_do_assunto():
    '''Falha da rodada de 2026-10-20: a busca semântica trouxe contratos de
    outro assunto, e a resposta disse que não havia contratos.'''
    caso = _caso_golden("P4.2")
    resposta = {
        "answer": "Não há contratos de manutenção de computadores nos registros apresentados.",
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is False


def test_p42_bases_esperadas_e_so_estruturada():
    '''Premissa verificada no SQLite: não existe registro vinculado na base
    agregada para essas compras — exigir "agregada" reprovaria a resposta
    correta.'''
    assert _caso_golden("P4.2").bases_esperadas == ["estruturada"]


def test_p43_aceita_resposta_completa_sem_dizer_insuficiencia():
    '''A cláusula "Se a evidência for insuficiente..." é CONDICIONAL: com
    evidência suficiente para responder a regra, reconhecer insuficiência
    não é obrigatório.'''
    caso = _caso_golden("P4.3")
    resposta = {
        "answer": "Quando a modalidade não aparece nos registros, o corpus "
        "determina que ela seja declarada como não informada; aplicam-se as "
        "regras gerais da Lei 14.133/2021, sem modalidade identificada.",
        "confidence_level": "media",
    }
    assert _content_ok(caso, resposta) is True


def test_p43_aceita_resposta_do_lote19():
    caso = _caso_golden("P4.3")
    resposta = {
        "answer": "A evidência disponível é insuficiente para responder com "
        "segurança; a modalidade de licitação não aparece em nenhum dos "
        "registros de contexto, de modo que não é possível associar regras a "
        "uma modalidade específica.",
        "confidence_level": "baixa",
    }
    assert _content_ok(caso, resposta) is True


def test_p43_reprova_tratar_nao_informado_como_modalidade_real():
    caso = _caso_golden("P4.3")
    resposta = {
        "answer": "Não informado foi a modalidade real dos três contratos, "
        "e a regra aplicável é a do pregão eletrônico.",
        "confidence_level": "alta",
    }
    assert _content_ok(caso, resposta) is False


def test_termo_de_negacao_nao_e_vetado_por_negacao_vizinha():
    '''Defeito #17 (caso real do lote19 em P4.2): em "a modalidade não consta
    nos registros, não há dados suficientes...", o "não" de "não consta"
    vetava o termo "não há dados suficientes" — e a resposta certa reprova.'''
    texto = ("Nos registros o campo modalidade não consta; não há dados "
             "suficientes para afirmar qualquer modalidade.")
    assert _termo_afirmado("não há dados suficientes", texto) is True
    assert _termo_afirmado("modalidade não consta", texto) is True


def test_termo_comum_continua_sendo_vetado_por_negacao():
    '''O veto externo segue valendo para termos afirmativos: "não há mediana"
    não afirma "mediana".'''
    assert _termo_afirmado("mediana", "não há mediana a informar") is False
    assert _termo_afirmado("predominante", "não é possível concluir predominante") is False


def test_p43_nao_pune_quem_esclarece_que_nao_informado_nao_e_modalidade():
    '''A frase "não informado como modalidade real" saiu do nao_deve_conter:
    puniria a retificação correta ("não se considera 'não informado' como
    modalidade real").'''
    caso = _caso_golden("P4.3")
    resposta = {
        "answer": "Quando a modalidade não aparece, o registro declara a "
        "lacuna: modalidade não informada. Não se considera 'não informado' "
        "como modalidade real; aplicam-se as regras gerais da Lei 14.133/2021.",
        "confidence_level": "media",
    }
    assert _content_ok(caso, resposta) is True


def _resposta_p12(texto):
    return {"answer": texto, "confidence_level": "baixa", "bases_consultadas": ["estruturada", "conversacional"],
            "is_refusal": False, "sources_used": [{"chunk_id": "c1"}]}


@pytest.mark.parametrize("texto", [
    "Não há evidência de atas ou registros mencionando atraso de entrega, descumprimento de prazo ou "
    "problema de fornecimento para os fornecedores de tecnologia estaduais de 2025 acima de R$ 500 mil.",
    "Nenhum dos registros mencionados no contexto refere-se a atraso de entrega ou problemas de fornecimento.",
])
def test_p12_aceita_a_resposta_da_v7(texto):
    '''Na v7 a premissa se confirma (Extreme Digital, 3 contratos; 3Structure IT, 7) e nenhuma
    ata desses fornecedores menciona atraso: o gabarito da v6 pedia "nenhum fornecedor".'''
    assert _content_ok(_caso_golden("P1.2"), _resposta_p12(texto)) is True


@pytest.mark.parametrize("texto", [
    "Nenhum fornecedor atende ao critério; a premissa não se confirma.",
    "Sim, há atraso de entrega registrado para a 3Structure IT.",
])
def test_p12_reprova_a_premissa_da_v6_e_o_atraso_inventado(texto):
    assert _content_ok(_caso_golden("P1.2"), _resposta_p12(texto)) is False


def test_p22_aceita_impossivel_calcular_como_insuficiencia():
    """Resposta real da rodada golden-2026-10-21-v7-36-anuladas: certa, com
    "tornando impossível calcular", forma que o gabarito ainda não aceitava."""
    resposta = {
        "answer": ("Os dados não fornecem informações sobre a quantidade de veículos em cada contrato, "
                   "tornando impossível calcular a mediana do valor por veículo."),
        "confidence_level": "baixa", "bases_consultadas": ["agregada", "estruturada"],
        "is_refusal": False, "sources_used": [{"chunk_id": "c1"}],
    }
    assert _content_ok(_caso_golden("P2.2"), resposta) is True


@pytest.mark.parametrize("answer, esperado", [
    # Resposta real da rodada golden-2026-10-23-v7-36-prompt-citacao: certa.
    ("Na contratação identificada no [1], a modalidade foi identificada como 'concorrência', "
     "regulamentada pela Lei 14.133/2021, Art. 28, II. No [2], a modalidade não está informada no "
     "registro. A amostra apresenta insuficiência para confirmar a identificação da modalidade em "
     "todos os registros de 2025.", True),
    # Respostas reais erradas das reexecuções: continuam reprovadas.
    ("Na amostra, apenas o registro [1] possui modalidade identificada: 'concorrência'. A amostra "
     "não é insuficiente, pois há pelo menos um exemplo com modalidade identificada.", False),
    ("A norma aplicável à modalidade 'concorrência' é a Lei 14.133/2021, Art. 28, II, conforme "
     "registrado no contrato [1]. Na amostra fornecida, apenas esse registro identifica a modalidade.", False),
])
def test_p32_aceita_insuficiencia_e_reprova_a_negacao(answer, esperado):
    resposta = {"answer": answer, "confidence_level": "media", "bases_consultadas": ["estruturada", "normativa"],
                "is_refusal": False, "sources_used": [{"chunk_id": "c1"}]}
    assert _content_ok(_caso_golden("P3.2"), resposta) is esperado


@pytest.mark.parametrize("termo, texto, afirmado", [
    # P3.2 de 2026-10-23: o "não" é da primeira oração; a insuficiência está afirmada.
    ("insuficiente", "O trecho [2] não identifica a modalidade, tornando insuficiente a amostra.", True),
    ("insuficiente", "Não há registro da modalidade, mas a amostra é insuficiente.", True),
    # Negação na mesma oração continua negação.
    ("insuficiente", "A amostra não é insuficiente, pois há um exemplo.", False),
    ("insuficiente", "Não é, portanto, insuficiente.", False),
    ("pregão foi a modalidade", "Os dados não mostram que o pregão foi a modalidade.", False),
])
def test_negacao_nao_atravessa_virgula_que_abre_outra_oracao(termo, texto, afirmado):
    from eval.run_eval import _termo_afirmado
    assert _termo_afirmado(termo, texto) is afirmado
