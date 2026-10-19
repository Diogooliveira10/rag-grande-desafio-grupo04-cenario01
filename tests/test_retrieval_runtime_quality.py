"""Busca em tempo de consulta: normas primárias e rede indisponível, sem alterar índices."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import config
from src import retrieve as ret


@pytest.fixture(autouse=True)
def reset_circuit(monkeypatch):
    ret._EMBEDDING_FAILURES.clear()
    # Os testes congelam time.monotonic (o módulo global). Com o horário da
    # última chamada real deixado por testes anteriores, aguardar_modelo
    # calculava ~14 h de espera e a suíte completa travava aqui.
    from src import obs
    monkeypatch.setattr(obs, "aguardar_modelo", lambda: None)
    yield
    ret._EMBEDDING_FAILURES.clear()


def response():
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[1.0, 0.0], index=0)],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=0, total_tokens=1),
    )


def test_embedding_timeout_e_cooldown_por_endpoint(monkeypatch):
    import openai
    calls, options = [], []
    monkeypatch.setattr(config, "QUERY_EMBEDDING_TIMEOUT", 2.5)
    monkeypatch.setattr(config, "QUERY_EMBEDDING_COOLDOWN", 30)
    clock = [100.0]
    monkeypatch.setattr(ret.time, "monotonic", lambda: clock[0])

    def create(**kwargs):
        calls.append(kwargs)
        raise ConnectionError("offline")

    def client(**kwargs):
        options.append(kwargs)
        return SimpleNamespace(embeddings=SimpleNamespace(create=create))

    monkeypatch.setattr(openai, "OpenAI", client)
    stats = {"calls": 0, "cache_hits": 0}
    embed = ret._embed_fn_com_uso([], stats)
    with pytest.raises(ConnectionError):
        embed(["primeira"])
    with pytest.raises(ret.EmbeddingUnavailable):
        embed(["segunda"])
    assert len(calls) == 1 and stats["calls"] == 1
    assert options[0]["timeout"].read == 2.5
    assert options[0]["timeout"].connect == 2.5
    assert options[0]["max_retries"] == 0
    clock[0] = 131.0
    with pytest.raises(ConnectionError):
        embed(["terceira"])
    assert len(calls) == 2


def test_erro_de_entrada_nao_abre_circuito(monkeypatch):
    import openai
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise ValueError("entrada inválida")
        return response()

    monkeypatch.setattr(openai, "OpenAI", lambda **_: SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    embed = ret._embed_fn_com_uso([])
    with pytest.raises(ValueError):
        embed(["entrada"])
    assert embed(["outra entrada"]) == [[1.0, 0.0]]
    assert not ret._EMBEDDING_FAILURES


def test_cache_continua_disponivel_com_circuito_aberto(monkeypatch):
    import openai
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise ConnectionError("offline")
        return response()

    monkeypatch.setattr(openai, "OpenAI", lambda **_: SimpleNamespace(embeddings=SimpleNamespace(create=create)))
    embed = ret._embed_fn_com_uso([])
    assert embed(["conhecida"]) == [[1.0, 0.0]]
    with pytest.raises(ConnectionError):
        embed(["nova"])
    assert embed(["conhecida"]) == [[1.0, 0.0]]
    assert len(calls) == 2


def record(cid, text, *, norma=None):
    return {"chunk_id": cid, "text": text, "base_id": "editais_normativo", "metadata": {
        "chunk_id": cid, "base_id": "editais_normativo", "natureza": "normativa", "doc_type": "pdf",
        "source_file": f"{cid}.pdf", "extras": {"numero_norma": norma} if norma else {},
    }}


def test_mencao_no_corpo_nao_e_identidade_da_norma():
    r = record("catalogo", "Lei 14.133/2021; Decreto 48.816/2023")
    assert ret._norma_do_documento(r["metadata"]) == set()
    r["metadata"]["extras"] = {"numero_norma": "Lei nº 14.133/2021"}
    assert ret._norma_do_documento(r["metadata"]) == {("lei", "14133")}


@pytest.mark.parametrize("rerank", [False, True])
def test_norma_primaria_precede_catalogo_e_decreto(tmp_path, monkeypatch, rerank):
    docs = tmp_path / "documents.jsonl"
    docs.write_text("\n".join(json.dumps(r) for r in [
        record("catalogo", "Lei 14.133/2021 " * 25),
        record("decreto", "Lei 14.133/2021 " * 10, norma="Decreto 48.816/2023"),
        record("lei", "Art. 1º. Esta lei estabelece normas gerais de licitação e contratação.", norma="Lei 14.133/2021"),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "DENSE_RETRIEVAL_ENABLED", False)
    resultados = ret.retrieve("O que estabelece a Lei 14.133/2021?", index_dir=tmp_path / "index",
                              documents_file=docs, top_k=1,
                              reranker=(lambda _, items: list(reversed(items))) if rerank else None)
    assert resultados[0].chunk_id == "lei"
    assert resultados[0].metadata["extras"]["recuperacao_degradada"] == "bm25_sem_embedding"


def test_desativar_denso_nao_chama_encoder(tmp_path, monkeypatch):
    docs = tmp_path / "documents.jsonl"
    docs.write_text(json.dumps(record("lei", "licitação eletrônica")) + "\n", encoding="utf-8")
    monkeypatch.setattr(config, "DENSE_RETRIEVAL_ENABLED", False)
    resultados = ret.retrieve("licitação", index_dir=tmp_path / "index", documents_file=docs,
                              embed_fn=lambda _: pytest.fail("Não deve chamar encoder"))
    assert resultados and resultados[0].chunk_id == "lei"


def test_prioridade_de_norma_nao_cria_registros_fora_das_evidencias():
    catalogo = ret._result(record("catalogo", "Lista de normas"), 10)
    assert ret._priorizar_norma_solicitada("Lei 14.133/2021", [catalogo], [], 1) == [catalogo]


def test_numero_da_lei_nao_domina_consulta_topica():
    assert ret._consulta_topica_norma("O que estabelece a Lei 14.133/2021 sobre as fases do processo licitatório?") == "fases processo licitatorio"


def test_visao_geral_prioriza_disposicoes_iniciais_e_diversifica_artigos():
    items = [ret._result(record(cid, text, norma="Lei 14.133/2021"), 10) for cid, text in [
        ("alteracao", "Art. 177. Altera outro código de processo."),
        ("artigo1", "Art. 1º Esta lei estabelece normas gerais."),
        ("artigo1_copia", "Art. 1º Esta lei estabelece normas gerais. Outra extração."),
        ("principios", "Art. 5º São observados os princípios."),
    ]]
    ranked = ret._ordenar_candidatos_norma("O que estabelece a Lei 14.133/2021?", items)
    assert [r.chunk_id for r in ranked[:2]] == ["artigo1", "principios"]


def test_fases_prioriza_enumeracao_em_vez_de_mencao_incidental():
    items = [ret._result(record(cid, text, norma="Lei 14.133/2021"), 10) for cid, text in [
        ("encerramento", "Art. 71. Encerradas as fases de julgamento."),
        ("enumeracao", "Art. 17. O processo observará as seguintes fases: I - preparatória; II - divulgação."),
    ]]
    assert ret._ordenar_candidatos_norma("Quais fases segundo a Lei 14.133/2021?", items)[0].chunk_id == "enumeracao"


def test_artigo_explicito_prevalece_sobre_visao_geral():
    items = [ret._result(record(cid, text, norma="Lei 14.133/2021"), 10) for cid, text in [
        ("artigo1", "Art. 1º Normas gerais."), ("artigo17", "Art. 17. Fases do processo."),
    ]]
    assert ret._ordenar_candidatos_norma("O que diz o artigo 17 da Lei 14.133/2021?", items)[0].chunk_id == "artigo17"
