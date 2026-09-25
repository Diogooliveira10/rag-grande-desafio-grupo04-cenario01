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
    "input", "output", "tokens", "modelo", "extra",
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
        assert r["extra"]["bases"] == ["contratos_estruturado"]
        assert r["extra"]["k"] == 5
    assert registros[0]["output"].endswith("top base: contratos_estruturado")


def test_retrieve_registra_rerank_quando_ha_reranker(indice):
    retrieve("contrato", None, reranker=lambda q, itens: list(reversed(itens)), **indice)
    assert _stages()[-1] == "rerank"


def test_retrieve_registra_erro_e_propaga_excecao(indice):
    def embed_quebrado(textos):
        raise ConnectionError("endpoint fora do ar")

    with pytest.raises(ConnectionError):
        retrieve("contrato", None, **{**indice, "embed_fn": embed_quebrado})

    [registro] = _registros()
    assert registro["stage"] == "retrieval_denso"
    assert registro["status"] == "erro"
    assert registro["extra"]["error_type"] == "ConnectionError"


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
    [registro] = _registros()
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

    [registro] = _registros()
    assert registro["status"] == "saida_invalida"
    assert "erro_parsing" in registro["extra"]


def test_cliente_padrao_usa_sdk_do_llm_ecoa(monkeypatch):
    import llm_ecoa

    chamadas = {}

    def create(**kwargs):
        chamadas.update(kwargs)
        return _resposta_sdk()

    monkeypatch.setattr(llm_ecoa, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))

    texto, tokens = gen._texto_e_tokens(gen._cliente_padrao()([{"role": "user", "content": "oi"}]))

    assert chamadas["model"] == gen.GENERATION_MODEL
    assert chamadas["messages"] == [{"role": "user", "content": "oi"}]
    assert json.loads(texto)["answer"] == "ok"
    assert tokens.total == 150


# --------------------------------------------------------------------------
# Ponta a ponta: run_id amarra todos os stages de uma pergunta
# --------------------------------------------------------------------------


def test_pipeline_amarra_todos_os_stages_pelo_mesmo_run_id(monkeypatch, indice):
    monkeypatch.setattr(pipeline, "retrieve", lambda q, rota, **kw: retrieve(q, rota, **indice, **kw))

    resposta = pipeline.responder(
        "Quais contratos de ambulancias foram publicados?",
        cliente=lambda mensagens: _resposta_sdk(sources=("c1",)),
    )

    assert resposta["is_refusal"] is False
    registros = _registros()
    assert [r["stage"] for r in registros] == [
        "query_analysis", "roteamento", "retrieval_denso", "retrieval_bm25", "fusao_rrf", "geracao",
    ]
    assert len({r["run_id"] for r in registros}) == 1
    assert len({r["query_id"] for r in registros}) == 1
    assert registros[0]["query_id"].startswith("q_")


def test_log_nao_grava_dado_pessoal_da_pergunta():
    rotear_pergunta("O contrato do CPF 123.456.789-09 foi publicado?")

    [registro] = _registros()
    assert "123.456.789-09" not in registro["input"]
    assert "[CPF omitido]" in registro["input"]