from __future__ import annotations

import json
from datetime import date

import pytest

from src import generate as gen
from src import obs
from src.schema import Chunk, ChunkMetadata, ConfidenceLevel, Natureza, RefusalReason


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
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA


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
    assert resposta.refusal_reason == RefusalReason.SEM_EVIDENCIA

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

    assert chunk.content == '{"objetoCompra":"Licença de software","valor":0}'


def test_chunks_entram_ate_o_limite_do_orcamento(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 250)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 50)
    chunks = [_chunk(chunk_id=f"c{i}", conteudo="x" * 200) for i in range(5)]

    selecionados = gen._chunks_no_orcamento(chunks)

    assert [c.metadata.chunk_id for c in selecionados] == ["c0", "c1"]
    assert selecionados[0].content == "x" * 50 + " [...]"


def test_primeiro_chunk_sempre_entra_mesmo_acima_do_orcamento(monkeypatch):
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 10)

    assert len(gen._chunks_no_orcamento([_chunk(conteudo="y" * 300)])) == 1


def test_instrucao_de_formato_fica_no_fim_do_prompt():
    [_, usuario] = gen._montar_prompt("pergunta", [_chunk()])

    assert usuario["content"].rstrip().endswith("os chunk_id usados.")
    assert "Responda somente com o objeto JSON" in usuario["content"]


def test_geracao_loga_quantos_chunks_couberam_no_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(obs.default_logger, "logs_dir", tmp_path)
    monkeypatch.setattr(gen.config, "GENERATION_CONTEXT_CHARS", 150)
    monkeypatch.setattr(gen.config, "GENERATION_CHUNK_CHARS", 100)
    chunks = [_chunk(chunk_id=f"c{i}", conteudo="z" * 100) for i in range(3)]
    vistos = {}

    def cliente(mensagens):
        vistos["prompt"] = mensagens[1]["content"]
        return json.dumps({"answer": "ok", "is_refusal": False, "confidence_level": "alta", "sources_used": ["c0"]})

    gen.gerar_resposta("pergunta", chunks, [Natureza.ESTRUTURADA], cliente=cliente)

    assert "chunk_id=c0" in vistos["prompt"] and "chunk_id=c2" not in vistos["prompt"]
    registro = next(
        r
        for r in map(json.loads, obs.default_logger.log_path().read_text(encoding="utf-8").splitlines())
        if r["stage"] == "geracao"
    )
    assert registro["extra"]["chunks_no_prompt"] == 1
    assert registro["extra"]["chunks_fora_do_orcamento"] == 2


def test_prompt_mostra_titulo_do_documento_e_vigencia():
    chunk = _chunk(chunk_id="c1")
    chunk.metadata.extras["titulo_documento"] = "Ata de Registro de Preços nº 00178"
    chunk.metadata.data_vigencia = date(2026, 10, 22)

    [_, usuario] = gen._montar_prompt("pergunta", [chunk])

    assert (
        "[chunk_id=c1 | base=contratos_estruturado | natureza=estruturada | "
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
