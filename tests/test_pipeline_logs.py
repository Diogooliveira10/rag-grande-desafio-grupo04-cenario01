"""Tópico 2.4: todo stage do pipeline grava o contrato de log em JSONL."""

import json
from types import SimpleNamespace

import pytest

from src import generate as gen
from src import obs, pipeline
from src.retrieve import retrieve
from src.router import rotear_pergunta
from src.schema import Chunk, ChunkMetadata, Natureza


CAMPOS_CONTRATO = {
    "ts", "run_id", "query_id", "stage", "status", "latencia_ms",
    "input", "output", "tokens", "modelo", "extra", "session_id",
    # Cadeia de hashes (src/log_chain.py), gravada junto com a linha.
    "hash_anterior", "hash",
}


def _registros():
    arquivos = list(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))
    assert len(arquivos) == 1
    return [json.loads(linha) for linha in arquivos[0].read_text(encoding="utf-8").splitlines()]


def _stages():
    return [r["stage"] for r in _registros()]


@pytest.fixture
def indice(tmp_path):
    """Índice mínimo com dois chunks da base estruturada."""
    chunks = [
        ("c1", "contrato de ambulancias da secretaria de saude", [1.0, 0.0]),
        ("c2", "contrato de material de escritorio", [0.0, 1.0]),
    ]
    index_dir = tmp_path / "index"
    (index_dir / "contratos_estruturado").mkdir(parents=True)
    documents = tmp_path / "documents.jsonl"
    linhas = []
    for chunk_id, texto, vetor in chunks:
        linhas.append({
            "chunk_id": chunk_id,
            "base_id": "contratos_estruturado",
            "text": texto,
            "embedding": vetor,
            "metadata": {
                "chunk_id": chunk_id, "base_id": "contratos_estruturado",
                "source_file": f"{chunk_id}.json", "doc_type": "json", "natureza": "estruturada",
            },
        })
    conteudo = "\n".join(json.dumps(l, ensure_ascii=False) for l in linhas)
    (index_dir / "contratos_estruturado" / "vectors.jsonl").write_text(conteudo, encoding="utf-8")
    documents.write_text(conteudo, encoding="utf-8")
    return {"index_dir": index_dir, "documents_file": documents, "embed_fn": lambda textos: [[1.0, 0.0]]}


def _resposta_sdk(answer="ok", sources=("c1",), prompt=120, completion=30):
    """Imita a resposta do SDK OpenAI (choices + usage)."""
    conteudo = json.dumps({
        "answer": answer, "is_refusal": False, "confidence_level": "alta", "sources_used": list(sources),
    })
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=conteudo))],
        usage=SimpleNamespace(prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion),
    )


# --------------------------------------------------------------------------
# Um teste por módulo
# --------------------------------------------------------------------------


def test_roteador_registra_stage_roteamento():
    rotear_pergunta("Quais contratos foram publicados?", run_id="run1", query_id="q_1")

    [registro] = _registros()
    assert set(registro) == CAMPOS_CONTRATO
    assert registro["stage"] == "roteamento"
    assert registro["status"] == "ok"
    assert registro["run_id"] == "run1" and registro["query_id"] == "q_1"
    assert registro["output"]["bases"] == ["estruturada"]


def test_retrieve_registra_denso_bm25_e_rrf(indice):
    resultados = retrieve(
        "contrato ambulancias", {"bases_selecionadas": ["estruturada"]},
        top_k=5, run_id="run1", query_id="q_1", **indice,
    )

    assert resultados
    registros = _registros()
    assert [r["stage"] for r in registros] == [
        "retrieval_denso", "retrieval_bm25", "fusao_rrf",
    ]
    for r in registros:
        assert set(r) == CAMPOS_CONTRATO
        assert r["status"] == "ok"
        assert r["run_id"] == "run1"
        assert r["extra"]["bases"] == ["estruturada"]
        assert r["extra"]["k"] == 5
    assert registros[0]["output"].endswith("top base: contratos_estruturado")


def test_retrieval_denso_registra_tokens_do_embedding_padrao(indice, monkeypatch):
    import openai
    import config

    chamadas = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.embeddings = SimpleNamespace(create=self._create)

        def _create(self, model, input):
            chamadas.append(model)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[1.0, 0.0]) for _ in input],
                usage=SimpleNamespace(prompt_tokens=7, total_tokens=7),
            )

    monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
    # Este teste cobre ECOA, não a seleção local herdada do .env da demo.
    monkeypatch.setattr(config, "EMBEDDING_PROVIDER", "ecoa")
    sem_embed_proprio = {k: v for k, v in indice.items() if k != "embed_fn"}

    retrieve("contrato de ambulancias", None, **sem_embed_proprio)

    denso = next(r for r in _registros() if r["stage"] == "retrieval_denso")
    assert chamadas == [config.EMBEDDING_MODEL]
    assert denso["tokens"] == {"prompt": 7, "completion": 0, "total": 7}
    assert denso["modelo"] == config.EMBEDDING_MODEL


def test_retrieval_denso_com_embed_fn_proprio_nao_inventa_tokens(indice):
    retrieve("contrato", None, **indice)

    denso = next(r for r in _registros() if r["stage"] == "retrieval_denso")
    assert denso["tokens"] == {"prompt": 0, "completion": 0, "total": 0}
    assert denso["modelo"] is None


def test_retrieve_registra_rerank_quando_ha_reranker(indice):
    retrieve("contrato", None, reranker=lambda q, itens: list(reversed(itens)), **indice)
    assert _stages()[-1] == "rerank"


def test_retrieve_registra_erro_e_propaga_excecao(indice):
    def embed_quebrado(textos):
        raise ValueError("vetor incompatível")

    with pytest.raises(ValueError):
        retrieve("contrato", None, **{**indice, "embed_fn": embed_quebrado})

    [registro] = _registros()
    assert registro["stage"] == "retrieval_denso"
    assert registro["status"] == "erro"
    assert registro["extra"]["error_type"] == "ValueError"


def test_retrieve_indisponivel_continua_bm25_sem_trocar_embedding(indice):
    def embed_quebrado(textos):
        raise ConnectionError("endpoint fora do ar")
    resultados = retrieve("contrato", None, **{**indice, "embed_fn": embed_quebrado})
    assert resultados
    assert all(item.metadata["extras"]["recuperacao_degradada"] == "bm25_sem_embedding" for item in resultados)
    denso = next(r for r in _registros() if r["stage"] == "retrieval_denso")
    assert denso["extra"]["degraded"] is True
    assert denso["extra"]["fallback"] == "bm25"


def test_geracao_registra_tokens_e_modelo():
    chunk = Chunk(
        content="contrato de ambulancias",
        metadata=ChunkMetadata(source_file="c1.json", chunk_id="c1", base_id="contratos_estruturado", doc_type="json"),
    )
    resposta = gen.gerar_resposta(
        "pergunta", [chunk], [Natureza.ESTRUTURADA],
        cliente=lambda mensagens: _resposta_sdk(), run_id="run1", query_id="q_1",
    )

    assert resposta.answer == "ok"
    registro, _validacao = _registros()
    assert set(registro) == CAMPOS_CONTRATO
    assert registro["stage"] == "geracao"
    assert registro["tokens"] == {"prompt": 120, "completion": 30, "total": 150}
    assert registro["modelo"] == gen.GENERATION_MODEL


def test_geracao_registra_saida_invalida_do_llm():
    chunk = Chunk(
        content="x",
        metadata=ChunkMetadata(source_file="c1.json", chunk_id="c1", base_id="b", doc_type="json"),
    )
    gen.gerar_resposta("pergunta", [chunk], [], cliente=lambda m: "isso não é json")

    registro, validacao = _registros()
    assert registro["status"] == "saida_invalida"
    assert "erro_parsing" in registro["extra"]
    assert validacao["stage"] == "validacao"
    assert validacao["status"] == "saida_invalida"
    assert validacao["extra"]["json_valido"] is False


def test_validacao_registra_citacao_descartada_sem_tokens():
    chunk = Chunk(
        content="contrato de ambulancias",
        metadata=ChunkMetadata(source_file="c1.json", chunk_id="c1", base_id="contratos_estruturado", doc_type="json"),
    )
    resposta = gen.gerar_resposta(
        "pergunta", [chunk], [Natureza.ESTRUTURADA],
        cliente=lambda mensagens: _resposta_sdk(sources=("c1", "inventado")), run_id="run1", query_id="q_1",
    )

    assert [s.chunk_id for s in resposta.sources_used] == ["c1"]
    geracao, validacao = _registros()
    assert set(validacao) == CAMPOS_CONTRATO
    assert validacao["stage"] == "validacao"
    assert validacao["status"] == "ok"
    assert (validacao["run_id"], validacao["query_id"]) == (geracao["run_id"], geracao["query_id"])
    assert validacao["extra"]["json_valido"] is True
    assert validacao["extra"]["citacoes_descartadas"] == ["inventado"]
    assert validacao["tokens"] == {"prompt": 0, "completion": 0, "total": 0}


def test_recusa_sem_chamar_llm_nao_registra_validacao():
    gen.gerar_resposta("pergunta", [], [Natureza.ESTRUTURADA], cliente=lambda m: _resposta_sdk())

    assert _stages() == ["geracao"]


def test_cliente_padrao_usa_sdk_do_llm_ecoa(monkeypatch):
    import llm_ecoa

    chamadas = {}

    def create(**kwargs):
        chamadas.update(kwargs)
        return _resposta_sdk()

    sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def with_options(**opcoes):
        chamadas["opcoes"] = opcoes
        return sdk

    monkeypatch.setattr(llm_ecoa, "client", SimpleNamespace(with_options=with_options))
    # Não deixar o .env desviar do SDK falso para uma chamada OpenRouter real.
    monkeypatch.setattr(gen.config, "LLM_PROVIDER_MODE", "auto")
    monkeypatch.setattr(gen.config, "OPENROUTER_API_KEY", "")

    texto, tokens = gen._texto_e_tokens(gen._cliente_padrao()([{"role": "user", "content": "oi"}]))

    # Sem timeout próprio, uma chamada travada no servidor esperava até 30 min.
    assert chamadas["opcoes"] == {"timeout": gen.config.GENERATION_TIMEOUT, "max_retries": gen.config.GENERATION_MAX_RETRIES}
    assert chamadas["model"] == gen.GENERATION_MODEL
    assert chamadas["messages"] == [{"role": "user", "content": "oi"}]
    assert chamadas["response_format"] == {"type": "json_object"}
    assert json.loads(texto)["answer"] == "ok"
    assert tokens.total == 150


# --------------------------------------------------------------------------
# Ponta a ponta: run_id amarra todos os stages de uma pergunta
# --------------------------------------------------------------------------


def test_pipeline_amarra_todos_os_stages_pelo_mesmo_run_id(monkeypatch, indice):
    monkeypatch.setattr(pipeline, "retrieve", lambda q, rota, **kw: retrieve(q, rota, **indice, **kw))

    # "Explique": a listagem pura sai do SQL; esta pergunta precisa da busca.
    resposta = pipeline.responder(
        "Quais contratos de ambulancias foram publicados? Explique o objeto.",
        cliente=lambda mensagens: _resposta_sdk(sources=("c1",)),
    )

    assert resposta["is_refusal"] is False
    registros = _registros()
    assert [r["stage"] for r in registros] == [
        "pipeline_inicio", "reformulacao", "interacao", "query_analysis", "roteamento",
        "retrieval_denso", "retrieval_bm25", "fusao_rrf", "rerank", "consulta_sql", "geracao", "validacao",
        "resumo_consulta", "pipeline_fim",
    ]
    assert len({r["run_id"] for r in registros}) == 1
    assert len({r["query_id"] for r in registros}) == 1
    assert registros[0]["query_id"].startswith("q_")


def test_log_nao_grava_dado_pessoal_da_pergunta():
    rotear_pergunta("O contrato do CPF 123.456.789-09 foi publicado?")

    [registro] = _registros()
    assert "123.456.789-09" not in registro["input"]
    assert "[CPF omitido]" in registro["input"]
