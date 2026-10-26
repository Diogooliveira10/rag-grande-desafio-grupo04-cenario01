from __future__ import annotations

import json
from datetime import date

import pytest

from src import generate as gen
from src import obs
from src.schema import Chunk, ChunkMetadata, ConfidenceLevel, Natureza, RAGResponse, RefusalReason


def _chunk(chunk_id="c1", base_id="contratos_estruturado", conteudo="texto do chunk"):
    return Chunk(
        content=conteudo,
        metadata=ChunkMetadata(
            source_file="arquivo.json",
            chunk_id=chunk_id,
            base_id=base_id,
            doc_type="json",
            natureza=Natureza.ESTRUTURADA,
        ),
    )






def test_publication_by_agencies_is_not_an_aggregate_request():
    from src.structured_query import requer_calculo
    assert not requer_calculo("Quais contratações de ambulâncias foram publicadas por órgãos públicos em 2025?")












def test_context_keeps_contract_evidence_before_long_administrative_fields(monkeypatch):
    record = {"administrativo": "x" * 2000, "numeroControlePNCP": "contrato-2025",
              "valorGlobal": 500123.45, "nomeRazaoSocialFornecedor": "Fornecedor público",
              "dataAssinatura": "2025-01-20", "objetoContrato": "Licenças de software"}
    original = json.dumps(record, ensure_ascii=False)
    assert json.loads(gen._compactar(original)) == record
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 400)
    chunk = _chunk(conteudo=original)
    context = gen._chunks_no_orcamento([chunk])[0].content
    assert "Licenças de software" in context
    assert "500123.45" in context
    assert "Fornecedor público" in context
    assert "2025-01-20" in context
    assert chunk.content == original


# --------------------------------------------------------------------------
# Guardrails determinísticos (não chamam o LLM)
# --------------------------------------------------------------------------

def test_guardrail_previo_bloqueia_pedido_de_dados_bancarios_sem_chamar_llm():
    chamou = {"vezes": 0}

    def cliente_espiao(mensagens):
        chamou["vezes"] += 1
        return "{}"

    resposta = gen.gerar_resposta(
        "Qual o número da conta bancária do fornecedor X?",
        [_chunk()],
        [Natureza.ESTRUTURADA],
        cliente=cliente_espiao,
    )

    assert chamou["vezes"] == 0
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.DADOS_BANCARIOS
    assert resposta.sources_used == []
    assert resposta.confidence_level == ConfidenceLevel.RECUSADO


def test_sem_chunks_recusa_sem_chamar_llm():
    chamou = {"vezes": 0}

    def cliente_espiao(mensagens):
        chamou["vezes"] += 1
        return "{}"

    resposta = gen.gerar_resposta("Qual o valor médio dos contratos?", [], [], cliente=cliente_espiao)

    assert chamou["vezes"] == 0
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA


# --------------------------------------------------------------------------
# Parsing da resposta do LLM (feliz e defensivo)
# --------------------------------------------------------------------------

def test_resposta_valida_com_fontes_reais():
    chunk = _chunk(chunk_id="c1", base_id="contratos_estruturado")
    chunk.metadata.doc_type = "sql"
    chunk.content = "3 registros; média R$ 4,20."

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "O valor médio foi R$ 4,20.",
            "is_refusal": False,
            "refusal_reason": None,
            "confidence_level": "alta",
            "sources_used": ["c1"],
        })

    resposta = gen.gerar_resposta(
        "Qual o valor médio?", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake,
    )

    assert resposta.is_refusal is False
    assert resposta.confidence_level == ConfidenceLevel.ALTA
    assert len(resposta.sources_used) == 1
    assert resposta.sources_used[0].base_id == "contratos_estruturado"
    assert resposta.sources_used[0].source_file == "arquivo.json"


def test_alias_de_evidencia_vira_chunk_id_real():
    chunk = _chunk(chunk_id="base:documento:chunk-197-ab12cd")
    prompt = {}

    def cliente_fake(mensagens):
        prompt["content"] = mensagens[1]["content"]
        return json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "alta",
            "sources_used": ["E1"],
        })

    resposta = gen.gerar_resposta(
        "pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake,
    )

    assert "evidence_id=E1" in prompt["content"]
    assert chunk.metadata.chunk_id not in prompt["content"]
    assert [source.chunk_id for source in resposta.sources_used] == [chunk.metadata.chunk_id]


def test_citacoes_aliasadas_exatas_nao_colidem_e_removem_repeticoes():
    chunks = [
        _chunk(chunk_id="base:chunk-1-abc"),
        _chunk(chunk_id="base:chunk-10-def"),
    ]

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "alta",
            "sources_used": ["E1", "E2", "E1", "E99"],
        })

    resposta = gen.gerar_resposta(
        "pergunta", chunks, [Natureza.ESTRUTURADA], cliente=cliente_fake,
    )

    assert [source.chunk_id for source in resposta.sources_used] == [
        "base:chunk-1-abc", "base:chunk-10-def",
    ]


@pytest.mark.parametrize(
    "referencia",
    ["chunk-197", "base:documento:chunk-197-inexistente", "E2"],
)
def test_citacao_parcial_ou_fora_do_contexto_e_descartada(referencia):
    chunk = _chunk(chunk_id="base:documento:chunk-197-ab12cd")

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "baixa",
            "sources_used": [referencia],
        })

    resposta = gen.gerar_resposta(
        "pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake,
    )

    assert resposta.sources_used == []


def test_citacao_repetida_por_id_real_e_alias_emite_uma_unica_fonte():
    chunk = _chunk(chunk_id="base:documento:chunk-197-ab12cd")

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "alta",
            "sources_used": ["E1", chunk.metadata.chunk_id, "E1"],
        })

    resposta = gen.gerar_resposta(
        "pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake,
    )

    assert [source.chunk_id for source in resposta.sources_used] == [chunk.metadata.chunk_id]


def test_lista_de_citacoes_vazia_nao_cria_fontes():
    resposta = gen.gerar_resposta(
        "pergunta",
        [_chunk(chunk_id="base:documento:chunk-197-ab12cd")],
        [Natureza.ESTRUTURADA],
        cliente=lambda mensagens: json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "baixa",
            "sources_used": [],
        }),
    )

    assert resposta.sources_used == []


def test_json_com_cercas_markdown_e_parseado():
    chunk = _chunk()

    def cliente_fake(mensagens):
        payload = json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "media",
            "sources_used": [],
        })
        return f"```json\n{payload}\n```"

    resposta = gen.gerar_resposta("pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.answer == "resposta"
    assert resposta.confidence_level == ConfidenceLevel.MEDIA


def test_chunk_id_inventado_pelo_llm_e_descartado():
    chunk = _chunk(chunk_id="c1")

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "resposta",
            "is_refusal": False,
            "confidence_level": "baixa",
            "sources_used": ["c1", "chunk-que-nao-existe"],
        })

    resposta = gen.gerar_resposta("pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert [s.chunk_id for s in resposta.sources_used] == ["c1"]


def test_chaves_alternativas_do_llm_sao_aceitas_e_registradas_no_log():
    """Saída real do qwen3-8b: "sources" e "confidence" numérico no lugar do contrato."""
    chunk = _chunk(chunk_id="c1")

    def cliente_fake(mensagens):
        return json.dumps({"answer": "resposta", "confidence": 0.95, "sources": ["c1"]})

    resposta = gen.gerar_resposta("pergunta", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake)

    assert [s.chunk_id for s in resposta.sources_used] == ["c1"]
    assert resposta.confidence_level == ConfidenceLevel.ALTA
    geracao, validacao = [
        json.loads(linha)
        for arquivo in obs.default_logger.logs_dir.glob("pipeline-*.jsonl")
        for linha in arquivo.read_text(encoding="utf-8").splitlines()
    ]
    assert geracao["extra"]["chaves_fora_do_contrato"] == ["confidence", "sources"]
    assert validacao["extra"]["chaves_fora_do_contrato"] == ["confidence", "sources"]


@pytest.mark.parametrize(
    ("confianca", "nivel"),
    [
        (0.95, ConfidenceLevel.ALTA),
        (0.8, ConfidenceLevel.ALTA),
        (0.6, ConfidenceLevel.MEDIA),
        (0.2, ConfidenceLevel.BAIXA),
        ("media", ConfidenceLevel.MEDIA),
        ("nao-sei", ConfidenceLevel.BAIXA),
        (None, ConfidenceLevel.BAIXA),
    ],
)
def test_confianca_numerica_ou_textual_vira_nivel(confianca, nivel):
    def cliente_fake(mensagens):
        return json.dumps({"answer": "resposta", "confidence": confianca, "sources": ["c1"]})

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.confidence_level == nivel


def test_saida_nao_json_cai_em_recusa_segura_sem_excecao():
    def cliente_fake(mensagens):
        return "desculpa, não sei responder isso em JSON."

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.ERRO_PROVEDOR
    assert "tente novamente" in resposta.answer.lower()
    assert "evidência" not in resposta.answer.lower()


def test_llm_diz_recusa_mas_inclui_fontes_e_corrigido():
    """O LLM errou o contrato (recusa citando fontes) — generate.py tem que corrigir,
    nunca deixar isso estourar a validação de src/schema.py."""
    chunk = _chunk(chunk_id="c1")

    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "não posso responder",
            "is_refusal": True,
            "refusal_reason": "juizo_legalidade_contrato",
            "confidence_level": "alta",
            "sources_used": ["c1"],
        })

    resposta = gen.gerar_resposta("Isso foi fraude?", [chunk], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.is_refusal is True
    assert resposta.sources_used == []
    assert resposta.confidence_level == ConfidenceLevel.RECUSADO
    assert resposta.refusal_reason == RefusalReason.JUIZO_LEGALIDADE


def test_confidence_recusado_sem_is_refusal_e_corrigido():
    def cliente_fake(mensagens):
        return json.dumps({
            "answer": "resposta parcial",
            "is_refusal": False,
            "confidence_level": "recusado",
            "sources_used": [],
        })

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_fake)
    assert resposta.is_refusal is False
    assert resposta.confidence_level == ConfidenceLevel.BAIXA


def test_erro_do_cliente_llm_nao_propaga_excecao():
    def cliente_com_erro(mensagens):
        raise ConnectionError("endpoint fora do ar")

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_com_erro)
    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.ERRO_PROVEDOR
    assert resposta.sources_used == []
    assert "tente novamente" in resposta.answer.lower()
    assert "evidência" not in resposta.answer.lower()


def test_erro_do_provedor_fica_registrado_no_log_da_geracao():
    def cliente_com_erro(mensagens):
        raise ConnectionError("endpoint fora do ar")

    gen.gerar_resposta(
        "pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente_com_erro, run_id="r-erro"
    )

    arquivo = next(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))
    geracao = [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines()
               if json.loads(l)["stage"] == "geracao"][-1]
    assert geracao["status"] == "erro_llm"
    assert geracao["output"] == "recusa: erro_provedor"
    assert "ConnectionError" in geracao["extra"]["erro"]


def test_sem_chunks_continua_sem_evidencia_e_nao_erro_de_provedor():
    resposta = gen.gerar_resposta("pergunta", [], [Natureza.ESTRUTURADA], cliente=lambda m: "{}")
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA


def test_llm_nao_consegue_marcar_erro_de_provedor():
    """Só o código marca falha de infraestrutura: se o modelo devolver esse motivo,
    ele vira sem_evidencia (e continua sujeito à conversão de recusa indevida)."""
    cliente = _llm_devolve(
        answer="Não há trecho sobre isso.", is_refusal=True,
        refusal_reason="erro_provedor", confidence_level="recusado",
    )
    resposta, erro, _, _ = gen._parsear_resposta_llm(
        cliente([]), "pergunta", [_chunk()], [Natureza.ESTRUTURADA]
    )
    assert erro is None
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA


def test_erro_de_provedor_nunca_e_convertido_em_resposta():
    assert RefusalReason.ERRO_PROVEDOR not in gen._RECUSAS_CONVERTIVEIS


def test_schema_do_llm_nao_oferece_erro_de_provedor():
    enum = gen._schema_resposta()["properties"]["refusal_reason"]["enum"]
    assert "erro_provedor" not in enum
    assert "sem_evidencia" in enum

# --------------------------------------------------------------------------
# Orçamento do contexto: a janela do endpoint da turma é de 2048 tokens e o
# Ollama corta o começo do prompt que passar disso.
# --------------------------------------------------------------------------

def test_registro_json_vai_compacto_e_sem_campos_vazios_para_o_prompt():
    registro = json.dumps(
        {"objetoCompra": "Licença de software", "linkSistemaOrigem": None, "fontes": [], "valor": 0},
        indent=2,
    )

    [chunk] = gen._chunks_no_orcamento([_chunk(conteudo=registro)])

    data = json.loads(chunk.content)
    assert data["objetoCompra"] == "Licença de software" and data["valor"] == 0
    assert "linkSistemaOrigem" not in data and "fontes" not in data
    assert data["modalidade_licitacao"] == "não informada neste registro"


def test_chunks_entram_ate_o_limite_do_orcamento(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 250)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 50)
    chunks = [_chunk(chunk_id=f"c{i}", conteudo="x" * 50) for i in range(5)]

    selecionados = gen._chunks_no_orcamento(chunks)

    assert [c.metadata.chunk_id for c in selecionados] == ["c0", "c1"]
    assert selecionados[0].content == "x" * 50


def test_primeiro_chunk_nao_fura_o_orcamento(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 10)

    assert gen._chunks_no_orcamento([_chunk(conteudo="y" * 300)]) == []


@pytest.mark.parametrize("posicao", [6, 7])
def test_chunk_relevante_em_posicao_tardia_entra_se_houver_orcamento(monkeypatch, posicao):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 2400)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 100)
    chunks = [_chunk(chunk_id=f"c{i}", conteudo=f"evidência {i}" * 5) for i in range(posicao)]
    selecionados = gen._chunks_no_orcamento(chunks)
    assert f"c{posicao - 1}" in [chunk.metadata.chunk_id for chunk in selecionados]


def test_orcamento_pula_cabecalho_longo_e_tenta_proxima_evidencia(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 500)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 100)
    primeiro = _chunk(chunk_id="cabivel", conteudo="curto")
    extenso = _chunk(chunk_id="cabecalho_extenso", conteudo="curto")
    extenso.metadata.extras["titulo_documento"] = "fonte-" + "x" * 1000
    ultimo = _chunk(chunk_id="relevante", conteudo="também curto")
    assert [c.metadata.chunk_id for c in gen._chunks_no_orcamento([primeiro, extenso, ultimo])] == ["cabivel", "relevante"]


def test_instrucao_de_formato_fica_no_fim_do_prompt():
    [_, usuario] = gen._montar_prompt("pergunta", [_chunk()])

    assert usuario["content"].rstrip().endswith("os evidence_id usados.")
    assert "Responda somente com o objeto JSON" in usuario["content"]


def test_geracao_loga_quantos_chunks_couberam_no_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(obs.default_logger, "logs_dir", tmp_path)
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 200)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 100)
    chunks = [_chunk(chunk_id=f"c{i}", conteudo="z" * 100) for i in range(3)]
    vistos = {}

    def cliente(mensagens):
        vistos["prompt"] = mensagens[1]["content"]
        return json.dumps({"answer": "ok", "is_refusal": False, "confidence_level": "alta", "sources_used": ["c0"]})

    gen.gerar_resposta("pergunta", chunks, [Natureza.ESTRUTURADA], cliente=cliente)

    assert "evidence_id=E1" in vistos["prompt"] and "evidence_id=E3" not in vistos["prompt"]
    registro = next(
        r
        for r in map(json.loads, obs.default_logger.log_path().read_text(encoding="utf-8").splitlines())
        if r["stage"] == "geracao"
    )
    assert registro["extra"]["chunks_no_prompt"] == 1
    assert registro["extra"]["chunks_fora_do_orcamento"] == 2
    assert registro["extra"]["prompt_chunk_ids"] == ["c0"]
    assert registro["extra"]["chunks_excluidos_orcamento"] == ["c1", "c2"]


def test_contexto_preserva_evidencia_multi_base_sem_exceder_orcamento(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 400)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 100)
    sql = _chunk("sql:contratacoes:listagem:hash:1", "contratos_estruturado", "s" * 90)
    sql.metadata.natureza = Natureza.ESTRUTURADA
    normativa = _chunk("norma:1", "editais_normativo", "n" * 90)
    normativa.metadata.natureza = Natureza.NORMATIVA
    extra_normativa = _chunk("norma:2", "editais_normativo", "x" * 90)
    extra_normativa.metadata.natureza = Natureza.NORMATIVA

    selecionados = gen._chunks_no_orcamento(
        [sql, normativa, extra_normativa],
        bases_requeridas=[Natureza.ESTRUTURADA, Natureza.NORMATIVA],
    )

    assert [chunk.metadata.base_id for chunk in selecionados] == [
        "contratos_estruturado", "editais_normativo",
    ]
    assert all(len(chunk.content) <= gen.config.GENERATION_CHUNK_CHARS + 6 for chunk in selecionados)


def test_cobertura_multi_base_nao_inventa_chunk_ausente():
    normativa = _chunk("norma:1", "editais_normativo", "evidência normativa")
    normativa.metadata.natureza = Natureza.NORMATIVA

    selecionados = gen._chunks_no_orcamento(
        [normativa],
        bases_requeridas=[Natureza.ESTRUTURADA, Natureza.NORMATIVA],
    )

    assert [chunk.metadata.base_id for chunk in selecionados] == ["editais_normativo"]


def test_prompt_mostra_titulo_do_documento_e_vigencia():
    chunk = _chunk(chunk_id="c1")
    chunk.metadata.extras["titulo_documento"] = "Ata de Registro de Preços nº 00178"
    chunk.metadata.data_vigencia = date(2026, 10, 22)

    [_, usuario] = gen._montar_prompt("pergunta", [chunk])

    assert (
        "[evidence_id=E1 | base=contratos_estruturado | natureza=estruturada | "
        "documento=Ata de Registro de Preços nº 00178 | vigencia_ate=2026-10-22]"
    ) in usuario["content"]


@pytest.mark.parametrize("query,answer,content", [
    ("Qual foi a mediana do valor por ambulância?", "Resultado do modelo.", '{"valorGlobal": 900000}'),
    ("Entre os fornecedores encontrados nesses contratos, existem atrasos?", "Resultado do modelo.", "Multa por atraso."),
    ("Microempresa pode participar?", "Sim, conforme o Decreto nº 42.063/2009.", "Art. 29. Bens comuns."),
    ("Qual a modalidade?", "Modalidade: Compras.", '{"tipoContrato": {"nome": "Compras"}}'),
    ("Uma microempresa pode participar?", "Sim.", "Decreto nº 42.063/2009. Decreto nº 42.092/2009."),
])
def test_removed_added_guardrails_preserve_llm_response(query, answer, content):
    calls = []
    def client(messages):
        calls.append(messages)
        return json.dumps({"answer": answer, "is_refusal": False, "refusal_reason": None,
                           "confidence_level": "alta", "sources_used": ["c1"]})
    response = gen.gerar_resposta(query, [_chunk(conteudo=content)],
                                 [Natureza.ESTRUTURADA, Natureza.NORMATIVA], cliente=client)
    assert len(calls) == 1
    assert not response.is_refusal
    assert response.answer == answer
    assert [source.chunk_id for source in response.sources_used] == ["c1"]


# --------------------------------------------------------------------------
# Recusa indevida do LLM numa pergunta no escopo (golden set de 30/09)
# --------------------------------------------------------------------------


def _llm_devolve(**saida):
    return lambda mensagens: json.dumps({"sources_used": [], **saida}, ensure_ascii=False)


@pytest.mark.parametrize("motivo", ["fora_de_escopo", "sem_evidencia"])
def test_recusa_com_texto_em_pergunta_no_escopo_vira_resposta_de_confianca_baixa(motivo):
    cliente = _llm_devolve(
        answer="O contexto só traz dados municipais, com mediana de R$ 519.500; falta a esfera estadual.",
        is_refusal=True, refusal_reason=motivo, confidence_level="recusado",
    )

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente)

    assert resposta.is_refusal is False
    assert resposta.refusal_reason is None
    assert resposta.confidence_level == ConfidenceLevel.BAIXA
    assert "mediana de R$ 519.500" in resposta.answer


def test_recusa_sem_texto_continua_recusa():
    cliente = _llm_devolve(answer="", is_refusal=True, refusal_reason="sem_evidencia", confidence_level="recusado")

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente)

    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA


def test_recusa_de_guardrail_do_llm_nunca_e_convertida():
    cliente = _llm_devolve(
        answer="Não posso afirmar se houve fraude.", is_refusal=True,
        refusal_reason="juizo_legalidade_contrato", confidence_level="recusado",
    )

    resposta = gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente)

    assert resposta.is_refusal is True
    assert resposta.refusal_reason == RefusalReason.JUIZO_LEGALIDADE


def test_conversao_de_recusa_fica_registrada_na_validacao():
    cliente = _llm_devolve(answer="Os dados mostram só parte do pedido.", is_refusal=True,
                           refusal_reason="fora_de_escopo", confidence_level="recusado")

    gen.gerar_resposta("pergunta", [_chunk()], [Natureza.ESTRUTURADA], cliente=cliente, run_id="r-conv")

    arquivo = next(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))
    validacao = [json.loads(l) for l in arquivo.read_text(encoding="utf-8").splitlines()
                 if json.loads(l)["stage"] == "validacao"][-1]
    assert validacao["extra"]["recusa_convertida"] == "fora_de_escopo"


def test_recusa_vazia_recebe_texto_e_url_publica():
    raw = json.dumps({'answer': '', 'is_refusal': True, 'refusal_reason': 'sem_evidencia', 'confidence_level': 'recusado', 'sources_used': []})
    resposta, erro, _, _ = gen._parsear_resposta_llm(raw, 'pergunta', [], [Natureza.ESTRUTURADA])
    assert resposta.answer.strip() and resposta.is_refusal and erro is None
    chunk = _chunk()
    chunk.metadata.origem = 'https://pncp.gov.br/api/consulta/v1/contratos'
    raw = json.dumps({'answer': 'Registro público', 'is_refusal': False, 'confidence_level': 'media', 'sources_used': ['c1']})
    resposta, erro, _, _ = gen._parsear_resposta_llm(raw, 'pergunta', [chunk], [Natureza.ESTRUTURADA])
    assert resposta.sources_used[0].origem == chunk.metadata.origem


def test_categoria_nao_vira_modalidade_e_json_nao_e_cortado(monkeypatch):
    registro = {'categoriaProcesso': {'nome': 'Compras'}, 'objetoContrato': 'Material', 'valorGlobal': 1200, 'administrativo': 'x' * 8000}
    chunk = _chunk(conteudo=json.dumps(registro))
    monkeypatch.setattr(gen.config, 'GENERATION_CONTEXT_CHARS', 1800)
    projected = gen._chunks_no_orcamento([chunk])[0]
    data = json.loads(projected.content)
    assert data['categoriaProcesso']['nome'] == 'Compras'
    assert data['modalidade_licitacao'] == 'não informada neste registro'
    assert data['valorGlobal'] == 1200 and 'administrativo' not in data
    assert chunk.content == json.dumps(registro)


def test_orcamento_reserva_evidencia_das_duas_intencoes(monkeypatch):
    a = _chunk('a', conteudo='a' * 500)
    a2 = _chunk('a2', conteudo='a' * 500)
    b = _chunk('b', base_id='editais_normativo', conteudo='norma ' * 80)
    a.metadata.extras['intencao'] = 0
    a2.metadata.extras['intencao'] = 0
    b.metadata.extras['intencao'] = 1
    monkeypatch.setattr(gen.config, 'GENERATION_CONTEXT_CHARS', 1250)
    chosen = gen._chunks_no_orcamento([a, a2, b])
    assert {c.metadata.chunk_id for c in chosen} == {'a', 'b'}


def test_schema_resposta_restringe_citacoes_ao_contexto():
    schema = gen._schema_resposta(['chunk-a', 'chunk-b'])
    assert schema['properties']['sources_used']['items']['enum'] == ['chunk-a', 'chunk-b']
    assert schema['properties']['answer']['minLength'] == 1
    assert schema['additionalProperties'] is False


# --------------------------------------------------------------------------
# Identificadores internos no texto viram [n], o número da fonte
# --------------------------------------------------------------------------

_SQL = "sql:contratacoes:estatistica:c3720065390a:1"
_ATA = "atas_conversacional:200b812bf0cb1853:chunk-1-7303119b4364"
_NORMA = "editais_normativo:1f7354fb65590678:chunk-10-f8902f79c194"
_ATA_PAGINA = "atas_conversacional:e88c7162ee580b8c:pagina-7:chunk-3-54656c243682"


def _responder_com(answer, fontes, chunks):
    return gen.gerar_resposta(
        "pergunta", chunks, [Natureza.ESTRUTURADA],
        cliente=lambda mensagens: json.dumps({
            "answer": answer, "is_refusal": False, "confidence_level": "alta", "sources_used": fontes,
        }),
    )


@pytest.mark.parametrize("answer, esperado", [
    # Os quatro formatos encontrados nas respostas reais do log e das avaliações.
    ("O órgão assinou 5 contratos em 2025, conforme a consulta SQL (evidence_id=E1).",
     "O órgão assinou 5 contratos em 2025, conforme a consulta SQL [1]."),
    ("Há registro de preços para computadores (chunk_id=" + _ATA + "). Nada mais.",
     "Há registro de preços para computadores [2]. Nada mais."),
    ("O trecho " + _SQL + " mostra 5 registros.", "O trecho [1] mostra 5 registros."),
    ("Vale o Decreto 48.816/2023 (chunks 10-f8902f79c194 e 1-7303119b4364).",
     "Vale o Decreto 48.816/2023 [2, 3]."),
    ("Total de R$ 3 bi [E1] e ata vigente [E2].", "Total de R$ 3 bi [1] e ata vigente [2]."),
    # Modelo de reserva do OpenRouter (2026-10-21): rótulo sem "=" nem ":".
    ("O trecho E2 prevê a convocação da microempresa.", "O trecho [2] prevê a convocação da microempresa."),
    # ECOA na P3.2 (golden de 2026-10-20): "registro E3", "contratação E1".
    ("A Lei 14.133 vale no registro E3; na contratação E1, a modalidade não consta.",
     "A Lei 14.133 vale no registro [3]; na contratação [1], a modalidade não consta."),
    # chunk_id de ata com página tem um segmento a mais.
    ("No chunk_id=" + _ATA_PAGINA + ", o fornecedor atrasou.", "No [2], o fornecedor atrasou."),
])
def test_ids_internos_no_texto_viram_o_numero_da_fonte(answer, esperado):
    chunks = [_chunk(_SQL), _chunk(_ATA, base_id="atas_conversacional"), _chunk(_NORMA, base_id="editais_normativo"),
              _chunk(_ATA_PAGINA, base_id="atas_conversacional")]
    if "Decreto" in answer:
        fontes = ["E1", "E3", "E2"]  # a ordem das fontes define o número
    elif _ATA_PAGINA in answer:
        fontes = ["E1", "E4"]
    else:
        fontes = ["E1", "E2", "E3"]

    resposta = _responder_com(answer, fontes, chunks)

    assert resposta.answer == esperado
    assert [f.chunk_id for f in resposta.sources_used][:1] == [_SQL]


def test_id_que_nao_e_fonte_aceita_sai_do_texto():
    resposta = _responder_com("Há 5 contratos (evidence_id=E2).", ["E1"], [_chunk(_SQL), _chunk(_ATA)])

    assert resposta.answer == "Há 5 contratos."


def test_texto_sem_ids_fica_igual_mesmo_com_letra_e_numero():
    # "E1" sem parênteses nem rótulo pode ser texto comum (classe E1, item E2).
    texto = "A classe E1 de servidores teve 3 contratos; o lote 10-A e o CNPJ 33.663.683/0001-16 constam."
    resposta = _responder_com(texto, ["E1"], [_chunk(_SQL)])

    assert resposta.answer == texto


def test_troca_de_ids_fica_registrada_no_log():
    _responder_com("Há 5 contratos (evidence_id=E1).", ["E1"], [_chunk(_SQL)])

    geracao = next(json.loads(l) for a in obs.default_logger.logs_dir.glob("pipeline-*.jsonl")
                   for l in a.read_text(encoding="utf-8").splitlines() if json.loads(l)["stage"] == "geracao")
    assert geracao["extra"]["ids_internos_no_texto"] == 1


def test_prompt_pede_evidence_id_entre_colchetes_e_nada_de_chunk_id():
    assert "[E1]" in gen._SYSTEM_PROMPT
    assert "não escreva chunk_id" in gen._SYSTEM_PROMPT


def test_amparo_legal_nao_vira_modalidade_do_contrato():
    # P4.2 (item 22): o contrato não traz modalidade, mas o amparo legal cita o
    # artigo do pregão; o LLM concluía "pregão predominante" a partir dele.
    contrato = json.dumps({
        "numeroControlePNCP": "29116902000170-2-000010/2025", "objetoContrato": "Manutenção de computadores",
        "categoriaProcesso": {"nome": "Serviços"},
        "amparoLegal": {"nome": "Lei 14.133/2021, Art. 28, I", "descricao": "pregão"},
    })
    prompt = {}

    def cliente(mensagens):
        prompt.update(sistema=mensagens[0]["content"], usuario=mensagens[1]["content"])
        return json.dumps({"answer": "A modalidade não está informada [E1].", "is_refusal": False,
                           "confidence_level": "baixa", "sources_used": ["E1"]})

    gen.gerar_resposta("Qual a modalidade predominante desses contratos?",
                       [_chunk(conteudo=contrato)], [Natureza.ESTRUTURADA], cliente=cliente)

    assert '"modalidade_licitacao":"não informada neste registro"' in prompt["usuario"]
    assert "Art. 28, I" in prompt["usuario"]  # a norma continua visível para a P4.3
    assert "Não a deduza do amparo legal" in prompt["sistema"]
    assert "nem a copie de registro de outro objeto ou processo" in prompt["sistema"]


def test_modalidade_da_compra_vinculada_aparece_no_registro_e_no_cabecalho():
    # Corpus v7: o contrato não traz modalidadeNome, e a modalidade vem da compra
    # vinculada. O registro dizia "não informada" e o cabeçalho, "Pregão".
    chunk = _chunk(conteudo=json.dumps({"objetoContrato": "Manutenção de computadores"}))
    chunk.metadata.extras.update(modalidade="Pregão - Eletrônico", modalidade_fonte="compra_vinculada")

    [preparado] = gen._chunks_no_orcamento([chunk])

    assert json.loads(preparado.content)["modalidade_licitacao"] == "Pregão - Eletrônico (da compra vinculada)"
    assert "modalidade=Pregão - Eletrônico (da compra vinculada)" in gen._cabecalho(preparado.metadata)


# --------------------------------------------------------------------------
# P3: confiabilidade no recorte, completude e cobertura separadas
# --------------------------------------------------------------------------


def _recorte(**mudancas):
    base = {
        "tabela": "contratacoes", "snapshot_versao": "6", "snapshot_hash": "abc",
        "registros_no_snapshot": 810, "registros_correspondentes": 4,
        "execucao": "completa", "filtros_relaxados": [], "calculo": "estatistica",
        "calculo_disponivel": True, "calculo_motivo": None,
        "tipo_resultado": "registros", "completude": [], "cobertura": "amostra",
    }
    base.update(mudancas)
    return base


def test_zero_confiavel_no_snapshot_nao_perde_confianca():
    """Zero correspondências com execução completa é conclusão, não falha."""
    nivel, avisos = gen._confianca_do_recorte(
        _recorte(registros_correspondentes=0, tipo_resultado="sem_correspondencia"))
    assert nivel == ConfidenceLevel.ALTA
    assert any("Escopo" in a for a in avisos)


def test_execucao_parcial_limita_confianca_pela_sua_propria_razao():
    nivel, avisos = gen._confianca_do_recorte(
        _recorte(execucao="parcial", filtros_relaxados=["termos de objeto unidos com OU em vez de E"]))
    assert nivel == ConfidenceLevel.MEDIA
    assert any("Execução parcial" in a and "OU" in a for a in avisos)


def test_calculo_indisponivel_nao_vira_zero():
    nivel, avisos = gen._confianca_do_recorte(
        _recorte(tipo_resultado="calculo_indisponivel", calculo_disponivel=False,
                 calculo_motivo="nenhum registro do recorte tem o campo de valor preenchido"))
    assert nivel == ConfidenceLevel.BAIXA
    assert any("campo de valor preenchido" in a for a in avisos)


def test_comparacao_entre_esferas_sem_dados_suficientes_e_baixa():
    nivel, avisos = gen._confianca_do_recorte(
        _recorte(tipo_resultado="comparacao_insuficiente",
                 calculo_motivo="Dados insuficientes: não é possível comparar as esferas neste recorte."))
    assert nivel == ConfidenceLevel.BAIXA
    assert any("não é possível comparar" in a for a in avisos)


def test_comparacao_insuficiente_limita_o_que_o_llm_afirmar():
    chunk = _chunk_com_recorte(_recorte(tipo_resultado="comparacao_insuficiente"))
    saida = gen._aplicar_recorte(_resposta_llm(ConfidenceLevel.ALTA), [chunk])
    assert saida.confidence_level == ConfidenceLevel.BAIXA


def test_soma_zero_e_diferente_de_sem_correspondencia():
    soma_zero = gen._confianca_do_recorte(_recorte(tipo_resultado="soma_zero"))[0]
    sem_registros = gen._confianca_do_recorte(
        _recorte(tipo_resultado="sem_correspondencia", registros_correspondentes=0))[0]
    assert soma_zero == ConfidenceLevel.ALTA and sem_registros == ConfidenceLevel.ALTA


def test_completude_vira_aviso_e_nao_baixa_a_confianca():
    """Campo ausente não é zero; a limitação é declarada à parte."""
    nivel, avisos = gen._confianca_do_recorte(
        _recorte(completude=[{"campo": "modalidade", "conhecidos": 2, "desconhecidos": 5}]))
    assert nivel == ConfidenceLevel.ALTA
    assert any("Completude" in a and "modalidade" in a for a in avisos)


def test_cobertura_declarada_sem_extrapolacao_estadual():
    _nivel, avisos = gen._confianca_do_recorte(_recorte())
    escopo = next(a for a in avisos if a.startswith("Escopo"))
    assert "não o universo de contratações do Estado" in escopo


def test_sem_recorte_nao_inventa_alta_confianca():
    nivel, avisos = gen._confianca_do_recorte({})
    assert nivel == ConfidenceLevel.MEDIA and avisos == []


def _chunk_com_recorte(recorte):
    return Chunk(
        content="Consulta SQL na tabela contratacoes: 1 registros",
        metadata=ChunkMetadata(
            source_file="base_estruturada.sqlite", chunk_id="sql:contratacoes:estatistica:x:1",
            base_id="contratos_estruturado", doc_type="sql", natureza=Natureza.ESTRUTURADA,
            chunking_strategy="consulta_sql",
            extras={"resultado_sql": {"cabecalho": "c", "linhas": [], "recorte": recorte}},
        ),
    )


def _resposta_llm(nivel, avisos=()):
    return RAGResponse(query="q", answer="a", bases_consultadas=[Natureza.ESTRUTURADA],
                       sources_used=[], confidence_level=nivel, is_refusal=False,
                       refusal_reason=None, avisos=list(avisos))


def test_recorte_completo_nao_eleva_confianca_baixa_do_llm():
    """O recorte completo não deve sobrepor a confiança explicitada pelo LLM."""
    chunk = _chunk_com_recorte(_recorte(registros_correspondentes=0,
                                        tipo_resultado="sem_correspondencia"))
    saida = gen._aplicar_recorte(_resposta_llm(ConfidenceLevel.BAIXA), [chunk])
    assert saida.confidence_level == ConfidenceLevel.BAIXA


def test_evidencia_parcial_declarada_pelo_llm_nao_elevada_para_alta():
    chunk = _chunk_com_recorte(_recorte())
    resposta = _resposta_llm(ConfidenceLevel.MEDIA).model_copy(update={
        "answer": "A evidência disponível é parcial; não permite confirmar todos os dados."
    })

    saida = gen._aplicar_recorte(resposta, [chunk])

    assert saida.confidence_level == ConfidenceLevel.MEDIA


def test_execucao_parcial_limita_o_que_o_llm_afirmar():
    chunk = _chunk_com_recorte(_recorte(execucao="parcial", filtros_relaxados=["termos relaxados"]))
    saida = gen._aplicar_recorte(_resposta_llm(ConfidenceLevel.ALTA), [chunk])
    assert saida.confidence_level == ConfidenceLevel.MEDIA


def test_calculo_indisponivel_limita_para_baixa():
    chunk = _chunk_com_recorte(_recorte(tipo_resultado="calculo_indisponivel",
                                        calculo_disponivel=False, calculo_motivo="sem campo de valor"))
    saida = gen._aplicar_recorte(_resposta_llm(ConfidenceLevel.ALTA), [chunk])
    assert saida.confidence_level == ConfidenceLevel.BAIXA


def test_recusa_nao_ganha_confianca_nem_avisos():
    recusa = RAGResponse(query="q", answer="a", bases_consultadas=[Natureza.ESTRUTURADA],
                         sources_used=[], confidence_level=ConfidenceLevel.RECUSADO,
                         is_refusal=True, refusal_reason=RefusalReason.SEM_EVIDENCIA)
    chunk = _chunk_com_recorte(_recorte())
    assert gen._aplicar_recorte(recusa, [chunk]) is recusa


def test_sem_recorte_o_llm_fica_com_o_que_disse():
    saida = gen._aplicar_recorte(_resposta_llm(ConfidenceLevel.MEDIA), [])
    assert saida.confidence_level == ConfidenceLevel.MEDIA


def test_avisos_do_recorte_somam_sem_duplicar():
    chunk = _chunk_com_recorte(_recorte())
    ja = ["Escopo: amostra do PNCP/RJ registrada neste snapshot, não o universo de contratações do Estado."]
    saida = gen._aplicar_recorte(_resposta_llm(ConfidenceLevel.ALTA, avisos=ja), [chunk])
    assert len(saida.avisos) == len(set(saida.avisos))
    assert any(a.startswith("Snapshot") for a in saida.avisos)


# --------------------------------------------------------------------------
# Orçamento do contexto: o top-k recuperado não pode sumir do prompt
# --------------------------------------------------------------------------

def test_orcamento_do_openrouter_cabe_o_top_k_inteiro(monkeypatch):
    """Defeito achado na P2.2: com 3500 chars, 7 dos 10 chunks do top-k eram
    descartados e o modelo declarava 'ausente' o que estava no topo do ranking.
    O orçamento do provedor de janela grande precisa comportar o top-k inteiro."""
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", gen.config.ORCAMENTO_CONTEXTO_OPENROUTER)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 900)
    chunks = [_chunk(chunk_id=f"c{i}", conteudo=f"registro {i} " + "x" * 850) for i in range(10)]

    selecionados = gen._chunks_no_orcamento(chunks)

    assert len(selecionados) == 10


def test_orcamento_janela_curta_fica_no_valor_do_endpoint_da_turma():
    """O endpoint da turma tem janela de 2048 tokens: o orçamento curto (3500
    chars, ~1.500 tokens com o system prompt) não pode subir junto."""
    assert gen.config.ORCAMENTO_CONTEXTO_JANELA_CURTA == 3500
    assert gen.config.ORCAMENTO_CONTEXTO_OPENROUTER == 12000


def test_representar_registro_de_ata_mantem_orgao_e_vigencia():
    '''Defeito #18: o registro de ata do PNCP identifica órgão/unidade e
    vigência como nomeOrgao/nomeUnidadeOrgao/vigencia*. Fora da lista de
    campos, a evidência do prompt ficava sem eles e a resposta honesta era
    "não consta", com o dado existindo no registro.'''
    from src.generate import _representar_registro

    registro = {
        "numeroControlePNCPAta": "28741080000155-1-000001/2023-000001",
        "objetoContratacao": "REGISTRO DE PREÇOS PARA AQUISIÇÃO DE APARELHOS DE AR CONDICIONADO.",
        "nomeOrgao": "MUNICIPIO DE ITABORAI",
        "nomeUnidadeOrgao": "PREFEITURA MUNICIPAL DE ITABORAI - RJ",
        "dataAssinatura": "2024-12-16",
        "dataPublicacaoPncp": "2025-02-12",
        "vigenciaInicio": "2025-02-13",
        "vigenciaFim": "2026-02-13",
    }
    saida = _representar_registro(json.dumps(registro, ensure_ascii=False))
    for campo in ("nomeOrgao", "nomeUnidadeOrgao", "vigenciaInicio", "vigenciaFim"):
        assert campo in saida, f"{campo} sumiu da evidência"
    # Os termos que o caso A1 exige ficam alcançáveis na evidência compactada.
    assert "MUNICIPIO DE ITABORAI" in saida
    assert "REGISTRO DE PREÇOS" in saida
    assert " - RJ" in saida


def test_representar_registro_continua_compactando_campos_desnecessarios():
    from src.generate import _representar_registro

    registro = {
        "numeroControlePNCP": "x", "objetoCompra": "Compra de teste",
        "linkSistemaOrigem": "https://exemplo.com", "chaveSemUso": "qualquer",
    }
    saida = _representar_registro(json.dumps(registro, ensure_ascii=False))
    assert "linkSistemaOrigem" not in saida
    assert "chaveSemUso" not in saida
    assert "Compra de teste" in saida


def _resposta_do_llm(texto: str, nivel: ConfidenceLevel = ConfidenceLevel.ALTA) -> RAGResponse:
    return RAGResponse(query="q", answer=texto, bases_consultadas=[Natureza.NORMATIVA], confidence_level=nivel)


@pytest.mark.parametrize("texto", [
    "Quando a modalidade não puder ser identificada, a amostra seria insuficiente para responder.",
    "Não há menção explícita à participação em pregão na Lei nº 14.133/2021.",
    "Os objetos não mencionam explicitamente manutenção de computadores, limitando a conclusão.",
    "Não há dados suficientes para concluir a predominância.",
    "Não é possível determinar a modalidade predominante.",
])
def test_resposta_que_admite_falta_de_dados_nao_sai_com_confianca_alta(texto):
    resposta = gen._limitar_confianca_pela_ressalva(_resposta_do_llm(texto))

    assert resposta.confidence_level == ConfidenceLevel.MEDIA
    assert gen._AVISO_RESSALVA in resposta.avisos


@pytest.mark.parametrize("texto", [
    "O município de Maricá tem 5 registros, total R$ 66.000,00 [1].",
    "A Lei 14.133/2021 estabelece normas gerais de licitação e contratos [1].",
    "Não há registros que atendam aos filtros; essa ausência não comprova que não existam contratações.",
])
def test_resposta_sem_ressalva_mantem_a_confianca(texto):
    assert gen._limitar_confianca_pela_ressalva(_resposta_do_llm(texto)).confidence_level == ConfidenceLevel.ALTA


def test_ressalva_nao_sobe_confianca_baixa():
    resposta = gen._limitar_confianca_pela_ressalva(_resposta_do_llm("Os dados são insuficientes.", ConfidenceLevel.BAIXA))

    assert resposta.confidence_level == ConfidenceLevel.BAIXA and resposta.avisos == []


def _chunk_de(natureza, n):
    base = {Natureza.ESTRUTURADA: "contratos_estruturado", Natureza.NORMATIVA: "editais_normativo"}[natureza]
    return Chunk(content="x" * 700, metadata=ChunkMetadata(
        chunk_id=f"{base}:{n}", base_id=base, source_file="f", doc_type="json", natureza=natureza))


def test_pergunta_mista_alterna_as_naturezas_no_orcamento(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 3500)
    sql = [_chunk_de(Natureza.ESTRUTURADA, i) for i in range(4)]
    normas = [_chunk_de(Natureza.NORMATIVA, i) for i in range(4)]

    escolhidos = gen._chunks_no_orcamento(sql + normas, [Natureza.ESTRUTURADA, Natureza.NORMATIVA])
    naturezas = [c.metadata.natureza for c in escolhidos]

    # Antes, as partes do SQL vinham primeiro e só 1 trecho normativo cabia.
    assert naturezas.count(Natureza.NORMATIVA) >= 2
    assert naturezas.count(Natureza.ESTRUTURADA) >= 2


def test_prompt_pede_declarar_a_lacuna_explicitamente():
    assert "os dados são insuficientes para" in gen._SYSTEM_PROMPT
    assert "não acrescente ressalva de insuficiência" in gen._SYSTEM_PROMPT



def test_trecho_mencionado_no_texto_vira_fonte_numerada():
    # Resposta real da P3.2 (golden de 2026-10-23): o texto falava de "registro
    # E1" e "E2" sem citá-los em sources_used, e o usuário via "E1" no texto.
    chunks = [_chunk("contrato-1"), _chunk("norma-2", base_id="editais_normativo"), _chunk("contrato-3")]
    resposta = _responder_com(
        "Apenas o registro [E3] tem modalidade: concorrência. O registro E1 não informa modalidade, "
        "e E2 é um documento normativo.", ["E3"], chunks)

    assert [f.chunk_id for f in resposta.sources_used] == ["contrato-3", "contrato-1", "norma-2"]
    assert resposta.answer == ("Apenas o registro [1] tem modalidade: concorrência. O registro [2] não "
                               "informa modalidade, e [3] é um documento normativo.")
    assert "E1" not in resposta.answer and "E2" not in resposta.answer


@pytest.mark.parametrize("texto", [
    "A classe E1 de servidores teve 3 contratos.",  # sem rótulo nem artigo antes do id
    "O registro E9 não informa modalidade.",          # E9 não existe no prompt
])
def test_mencao_que_nao_e_trecho_do_prompt_nao_vira_fonte(texto):
    resposta = _responder_com(texto, ["E1"], [_chunk("c1")])

    assert [f.chunk_id for f in resposta.sources_used] == ["c1"]
    assert resposta.answer == texto


def test_recusa_nao_ganha_fonte_por_mencao():
    resposta = gen._incluir_trechos_mencionados(RAGResponse(
        query="q", answer="O registro E1 não tem relação com a pergunta.", bases_consultadas=[],
        confidence_level=ConfidenceLevel.RECUSADO, is_refusal=True,
        refusal_reason=RefusalReason.SEM_EVIDENCIA), [_chunk("c1")])

    assert resposta.sources_used == []


def test_trecho_mencionado_depois_de_preposicao_vira_fonte():
    # Resposta real da P3.2 (golden final de 2026-10-23): "em E3", "como E1".
    chunks = [_chunk("contrato-1"), _chunk("norma-2", base_id="editais_normativo"), _chunk("contrato-3")]
    resposta = _responder_com(
        "A norma que se aplica à contratação identificada em E3 é a Lei 14.133/2021. "
        "Para outros registros, como E1, não há informações suficientes sobre a modalidade.", [], chunks)

    assert [f.chunk_id for f in resposta.sources_used] == ["contrato-3", "contrato-1"]
    assert resposta.answer == ("A norma que se aplica à contratação identificada em [1] é a Lei 14.133/2021. "
                               "Para outros registros, como [2], não há informações suficientes sobre a modalidade.")


@pytest.mark.parametrize("answer, esperado", [
    # Formas reais das respostas do golden de 2026-10-23.
    ("A Instrução Normativa 65/2021, mencionada no documento E2, trata disso.",
     "A Instrução Normativa 65/2021, mencionada no documento [1], trata disso."),
    ("O que torna inaplicável a regra de E2 para esses casos.",
     "O que torna inaplicável a regra de [1] para esses casos."),
    ("Os documentos que tratam desse procedimento são E2 e E3.",
     "Os documentos que tratam desse procedimento são [1] e [2]."),
    ("Não dá para identificar todas, pois apenas E3 apresenta informação clara.",
     "Não dá para identificar todas, pois apenas [1] apresenta informação clara."),
])
def test_outras_formas_de_mencao_viram_fonte(answer, esperado):
    chunks = [_chunk("c1"), _chunk("norma-2", base_id="editais_normativo"), _chunk("contrato-3")]
    assert _responder_com(answer, [], chunks).answer == esperado


def test_lista_depois_de_citacao_numerada():
    chunks = [_chunk("c1"), _chunk("c2"), _chunk("c3"), _chunk("c4")]
    resposta = _responder_com("Os registros [E2], E3 e E4 usam a modalidade.", ["E2"], chunks)
    assert resposta.answer == "Os registros [1], [2] e [3] usam a modalidade."


def _cliente_em_sequencia(*respostas):
    chamadas = []

    def cliente(mensagens):
        chamadas.append(mensagens)
        resposta = respostas[min(len(chamadas), len(respostas)) - 1]
        if isinstance(resposta, Exception):
            raise resposta
        return json.dumps(resposta)

    cliente.chamadas = chamadas
    return cliente


_SEM_FONTE = {"answer": "Não há dados suficientes: os trechos tratam de normas gerais.",
              "is_refusal": False, "confidence_level": "baixa", "sources_used": []}
_COM_FONTE = {"answer": "Não há dados suficientes: os trechos [E1] tratam de normas gerais.",
              "is_refusal": False, "confidence_level": "baixa", "sources_used": ["E1"]}


def test_resposta_sem_fonte_pede_citacao_uma_vez():
    # P4.3 do golden de 2026-10-23: resposta certa, sem nenhuma fonte citada.
    cliente = _cliente_em_sequencia(_SEM_FONTE, _COM_FONTE)
    resposta = gen.gerar_resposta("pergunta", [_chunk("norma-1")], [Natureza.NORMATIVA], cliente=cliente)

    assert len(cliente.chamadas) == 2
    assert cliente.chamadas[1][-1]["content"] == gen._PEDIDO_DE_CITACAO
    assert [f.chunk_id for f in resposta.sources_used] == ["norma-1"]
    assert resposta.answer == "Não há dados suficientes: os trechos [1] tratam de normas gerais."


@pytest.mark.parametrize("segunda", [_SEM_FONTE, RuntimeError("ECOA fora")])
def test_retentativa_que_nao_cita_mantem_a_original(segunda):
    cliente = _cliente_em_sequencia(_SEM_FONTE, segunda)
    resposta = gen.gerar_resposta("pergunta", [_chunk("norma-1")], [Natureza.NORMATIVA], cliente=cliente)

    assert len(cliente.chamadas) == 2
    assert resposta.answer == _SEM_FONTE["answer"] and resposta.sources_used == []


def test_resposta_com_fonte_nao_repete_a_chamada():
    cliente = _cliente_em_sequencia(_COM_FONTE)
    gen.gerar_resposta("pergunta", [_chunk("norma-1")], [Natureza.NORMATIVA], cliente=cliente)
    assert len(cliente.chamadas) == 1


def test_recusa_nao_pede_citacao():
    recusa = {"answer": "Fora do escopo.", "is_refusal": True, "refusal_reason": "fora_de_escopo",
              "confidence_level": "recusado", "sources_used": []}
    cliente = _cliente_em_sequencia(recusa)
    gen.gerar_resposta("pergunta", [_chunk("norma-1")], [], cliente=cliente)
    assert len(cliente.chamadas) == 1


def test_retentativa_fica_no_log_da_geracao():
    gen.gerar_resposta("pergunta", [_chunk("norma-1")], [Natureza.NORMATIVA],
                       cliente=_cliente_em_sequencia(_SEM_FONTE, _COM_FONTE))
    geracao = next(json.loads(l) for a in obs.default_logger.logs_dir.glob("pipeline-*.jsonl")
                   for l in a.read_text(encoding="utf-8").splitlines() if json.loads(l)["stage"] == "geracao")
    assert geracao["extra"]["retentativa_citacao"] == {"motivo": "resposta_sem_fontes", "resultado": "citou",
                                                       "fontes": ["norma-1"]}


def test_recusa_indevida_convertida_tambem_pede_citacao():
    # P4.3 de 2026-10-24: o LLM marcou "fora de escopo" e escreveu a resposta;
    # a conversão a tornava resposta, mas sem nenhuma fonte.
    recusa_com_texto = {"answer": "Não há dados suficientes: os trechos tratam de normas gerais.",
                        "is_refusal": True, "refusal_reason": "fora_de_escopo",
                        "confidence_level": "recusado", "sources_used": []}
    cliente = _cliente_em_sequencia(recusa_com_texto, _COM_FONTE)
    resposta = gen.gerar_resposta("pergunta", [_chunk("norma-1")], [Natureza.NORMATIVA], cliente=cliente)

    assert len(cliente.chamadas) == 2
    assert resposta.is_refusal is False
    assert [f.chunk_id for f in resposta.sources_used] == ["norma-1"]


def test_evidencia_insuficiente_nao_e_recusa_no_prompt_e_no_pedido_de_citacao():
    # P4.3 de 24 a 26/10: com trechos relacionados mas insuficientes, o LLM marcava
    # recusa e, por isso, devolvia as fontes vazias, inclusive no pedido de citação.
    assert "Evidência insuficiente não é recusa" in gen._SYSTEM_PROMPT
    assert "reconhecê-la é a resposta, não uma recusa" in gen._SYSTEM_PROMPT
    assert "não é uma recusa" in gen._PEDIDO_DE_CITACAO
    assert "is_refusal=false" in gen._PEDIDO_DE_CITACAO
