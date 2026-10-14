"""Estágio rerank: cross-encoder plugado no retrieve, sem baixar modelo nos testes."""

import json

import pytest

import config
from src import obs
from src.rerank import CrossEncoderReranker, EcoaReranker, reranker_padrao, texto_para_rerank
from src.retrieve import RetrievalResult, retrieve


def _resultado(chunk_id, texto, score):
    return RetrievalResult(chunk_id=chunk_id, content=texto, metadata={}, score=score, rrf_score=score)


def _nota_por_palavra(palavra):
    """score_fn falso: 1.0 quando o texto contém a palavra, 0.0 senão."""
    return lambda pares: [1.0 if palavra in texto else 0.0 for _, texto in pares]


@pytest.fixture
def indice(tmp_path):
    """Seis chunks: o que fala de microempresa é o último para denso e BM25."""
    textos = [f"contrato de ambulancias numero {i}" for i in range(5)] + [
        "microempresa pode participar de pregao"
    ]
    index_dir = tmp_path / "index"
    (index_dir / "contratos_estruturado").mkdir(parents=True)
    linhas = []
    for i, texto in enumerate(textos):
        chunk_id = f"c{i}"
        linhas.append({
            "chunk_id": chunk_id,
            "base_id": "contratos_estruturado",
            "text": texto,
            "embedding": [1.0, 0.0] if i < 5 else [0.0, 1.0],
            "metadata": {
                "chunk_id": chunk_id, "base_id": "contratos_estruturado",
                "source_file": f"{chunk_id}.json", "doc_type": "json", "natureza": "estruturada",
            },
        })
    conteudo = "\n".join(json.dumps(linha, ensure_ascii=False) for linha in linhas)
    (index_dir / "contratos_estruturado" / "vectors.jsonl").write_text(conteudo, encoding="utf-8")
    documents = tmp_path / "documents.jsonl"
    documents.write_text(conteudo, encoding="utf-8")
    return {"index_dir": index_dir, "documents_file": documents, "embed_fn": lambda textos: [[1.0, 0.0]]}


def _registros():
    arquivo = next(obs.default_logger.logs_dir.glob("pipeline-*.jsonl"))
    return [json.loads(linha) for linha in arquivo.read_text(encoding="utf-8").splitlines()]


def test_reranker_reordena_pela_nota_e_guarda_a_do_rrf():
    reranker = CrossEncoderReranker(score_fn=_nota_por_palavra("microempresa"))
    entrada = [_resultado("a", "ambulancia", 0.9), _resultado("b", "microempresa", 0.1)]

    saida = reranker("pergunta", entrada)

    assert [item.chunk_id for item in saida] == ["b", "a"]
    assert saida[0].score == 1.0
    assert saida[0].rerank_score == 1.0
    assert saida[0].rrf_score == 0.1
    assert entrada[0].score == 0.9  # não altera os resultados recebidos


def test_reranker_sem_resultados_nao_chama_o_modelo():
    def falha(_pares):
        raise AssertionError("não deveria pontuar lista vazia")

    assert CrossEncoderReranker(score_fn=falha)("pergunta", []) == []


def test_reranker_rejeita_numero_de_notas_diferente():
    reranker = CrossEncoderReranker(score_fn=lambda pares: [1.0])

    with pytest.raises(ValueError, match="1 notas para 2 chunks"):
        reranker("pergunta", [_resultado("a", "x", 1.0), _resultado("b", "y", 0.5)])


def test_reranker_padrao_respeita_rerank_enabled(monkeypatch):
    monkeypatch.setattr(config, "RERANK_ENABLED", False)
    assert reranker_padrao() is None

    monkeypatch.setattr(config, "RERANK_ENABLED", True)
    monkeypatch.setattr(config, "RERANK_MODEL", "modelo-x")
    monkeypatch.setattr(config, "RERANK_PROVIDER", "local")
    reranker = reranker_padrao()
    assert type(reranker) is CrossEncoderReranker
    assert reranker.modelo == "modelo-x"

    monkeypatch.setattr(config, "RERANK_PROVIDER", "ecoa")
    reranker = reranker_padrao()
    assert isinstance(reranker, EcoaReranker)
    assert reranker.fallback.modelo == "modelo-x"


def test_retrieve_entrega_mais_candidatos_ao_reranker_do_que_top_k(indice):
    """O chunk certo fica fora do top 2 do RRF; o rerank sobre 6 candidatos o traz ao topo."""
    sem_rerank = retrieve("contrato microempresa", None, top_k=2, **indice)
    assert "c5" not in [item.chunk_id for item in sem_rerank]

    reranker = CrossEncoderReranker("modelo-teste", score_fn=_nota_por_palavra("microempresa"))
    com_rerank = retrieve("contrato microempresa", None, top_k=2, reranker=reranker, rerank_k=6, **indice)

    assert len(com_rerank) == 2
    assert com_rerank[0].chunk_id == "c5"
    rerank = [r for r in _registros() if r["stage"] == "rerank"][-1]
    assert rerank["status"] == "ok"
    assert rerank["modelo"] == "modelo-teste"
    assert rerank["extra"]["rerank_k"] == 6


def test_retrieve_volta_para_o_rrf_quando_o_reranker_falha(indice):
    def quebrado(_query, _resultados):
        raise OSError("modelo indisponível")

    sem_rerank = retrieve("contrato", None, top_k=2, **indice)
    com_falha = retrieve("contrato", None, top_k=2, reranker=quebrado, **indice)

    assert [item.chunk_id for item in com_falha] == [item.chunk_id for item in sem_rerank]
    rerank = [r for r in _registros() if r["stage"] == "rerank"][-1]
    assert rerank["status"] == "erro"
    assert rerank["extra"]["error_type"] == "OSError"


def test_texto_para_rerank_poe_o_objeto_do_json_na_frente():
    conteudo = json.dumps(
        {
            "amparoLegal": {"codigo": 2, "descricao": "pregão"},
            "anoCompra": 2025,
            "linkSistemaOrigem": "https://exemplo.gov.br/x",
            "dataEncerramento": None,
            "objetoCompra": "Curso de pós-graduação em Gestão Pública",
            "valorTotalEstimado": 150000.5,
        },
        ensure_ascii=False,
        indent=2,
    )

    texto = texto_para_rerank(conteudo)

    assert texto.startswith("objetoCompra: Curso de pós-graduação em Gestão Pública")
    assert "descricao: pregão" in texto
    assert "valorTotalEstimado: 150000.5" in texto
    assert "https://" not in texto
    assert "dataEncerramento" not in texto


def test_texto_para_rerank_mantem_texto_comum_e_json_truncado():
    assert texto_para_rerank("Art. 94. A divulgação no PNCP") == "Art. 94. A divulgação no PNCP"
    truncado = '{ "anoCompra": 2025, "objetoCompra": "Reforma da escola", "orgao'
    assert texto_para_rerank(truncado).startswith("objetoCompra: Reforma da escola")


def test_reranker_pontua_o_texto_compacto_do_json():
    vistos = []

    def score_fn(pares):
        vistos.extend(texto for _, texto in pares)
        return [0.0 for _ in pares]

    conteudo = '{"anoCompra": 2025, "objetoCompra": "ambulancia tipo D"}'
    CrossEncoderReranker(score_fn=score_fn)("pergunta", [_resultado("a", conteudo, 1.0)])

    assert vistos == ["objetoCompra: ambulancia tipo D; anoCompra: 2025"]


def test_pipeline_usa_o_reranker_padrao_quando_nenhum_e_passado(monkeypatch):
    from src import pipeline
    from src.schema import ConfidenceLevel, RAGResponse

    usados = []

    def fake_retrieve(query, route, **kwargs):
        usados.append(kwargs["reranker"])
        return []

    sentinela = CrossEncoderReranker(score_fn=lambda pares: [])
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "reranker_padrao", lambda: sentinela)
    monkeypatch.setattr(pipeline, "consultar_estruturado", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "gerar_resposta", lambda *a, **k: RAGResponse(
        query="q", answer="sem evidência", bases_consultadas=[], confidence_level=ConfidenceLevel.BAIXA))

    pipeline.responder("Quais contratos de ambulancias foram publicados?")

    assert usados and all(item is sentinela for item in usados)


def test_pipeline_sem_rerank_quando_desligado(monkeypatch):
    from src import pipeline
    from src.schema import ConfidenceLevel, RAGResponse

    usados = []
    monkeypatch.setattr(config, "RERANK_ENABLED", False)
    monkeypatch.setattr(pipeline, "retrieve", lambda q, r, **kw: usados.append(kw["reranker"]) or [])
    monkeypatch.setattr(pipeline, "consultar_estruturado", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "gerar_resposta", lambda *a, **k: RAGResponse(
        query="q", answer="x", bases_consultadas=[], confidence_level=ConfidenceLevel.BAIXA))

    pipeline.responder("Quais contratos de ambulancias foram publicados?")

    assert usados == [None]


# --------------------------------------------------------------------------
# Endpoint /api/rerank do servidor da turma (sem rede: post_fn falso)
# --------------------------------------------------------------------------


def _servidor_por_palavra(palavra, chamadas=None):
    """Imita o /api/rerank: nota 0.9 para quem contém a palavra, 0.1 senão, fora de ordem."""
    def post(url, corpo, timeout):
        if chamadas is not None:
            chamadas.append((url, corpo, timeout))
        notas = [0.9 if palavra in doc else 0.1 for doc in corpo["documents"]]
        resultados = sorted(
            ({"index": i, "document": doc, "relevance_score": n} for i, (doc, n) in enumerate(zip(corpo["documents"], notas))),
            key=lambda item: -item["relevance_score"],
        )
        return {"model": "xagi/qwen3-embedding:0.6b-q4_k_m", "results": resultados}
    return post


def _servidor_fora_do_ar(url, corpo, timeout):
    raise TimeoutError("timed out")


def test_ecoa_reordena_pelas_notas_do_endpoint_e_manda_o_corpo_esperado():
    chamadas = []
    reranker = EcoaReranker(url="http://servidor/api/rerank", timeout=7,
                            post_fn=_servidor_por_palavra("microempresa", chamadas))
    candidatos = [_resultado("a", "contrato de ambulancias", 0.9), _resultado("b", "microempresa no pregao", 0.1)]

    reordenados = reranker("microempresa", candidatos)

    assert [r.chunk_id for r in reordenados] == ["b", "a"]
    assert [r.rerank_score for r in reordenados] == [0.9, 0.1]
    [(url, corpo, timeout)] = chamadas
    assert url == "http://servidor/api/rerank" and timeout == 7
    assert corpo == {"query": "microempresa", "documents": ["contrato de ambulancias", "microempresa no pregao"],
                     "top_n": 2}
    assert reranker.backend_usado == reranker.modelo == "ecoa:xagi/qwen3-embedding:0.6b-q4_k_m"


def test_ecoa_fora_do_ar_usa_o_cross_encoder_local():
    local = CrossEncoderReranker("modelo-local", score_fn=_nota_por_palavra("microempresa"))
    reranker = EcoaReranker(url="http://servidor/api/rerank", post_fn=_servidor_fora_do_ar, fallback=local)

    reordenados = reranker("microempresa", [_resultado("a", "ambulancias", 0.9), _resultado("b", "microempresa", 0.1)])

    assert [r.chunk_id for r in reordenados] == ["b", "a"]
    assert reranker.backend_usado == "local:modelo-local (ecoa falhou: TimeoutError)"


def test_ecoa_sem_fallback_propaga_o_erro():
    reranker = EcoaReranker(url="http://servidor/api/rerank", post_fn=_servidor_fora_do_ar)

    with pytest.raises(TimeoutError):
        reranker("q", [_resultado("a", "texto", 0.5)])


def test_ecoa_com_nota_faltando_e_erro_e_cai_no_fallback():
    def incompleto(url, corpo, timeout):
        return {"results": [{"index": 0, "relevance_score": 0.5}]}
    local = CrossEncoderReranker("modelo-local", score_fn=lambda pares: [0.0] * len(pares))
    reranker = EcoaReranker(url="http://servidor/api/rerank", post_fn=incompleto, fallback=local)

    reranker("q", [_resultado("a", "x", 0.5), _resultado("b", "y", 0.4)])

    assert reranker.backend_usado.startswith("local:modelo-local (ecoa falhou: ValueError")


def test_ecoa_corta_textos_longos():
    chamadas = []
    reranker = EcoaReranker(url="http://servidor/api/rerank", post_fn=_servidor_por_palavra("x", chamadas))

    reranker("q", [_resultado("a", "x" * 5000, 0.5)])

    assert len(chamadas[0][1]["documents"][0]) == 2000


def test_retrieve_registra_no_log_qual_backend_deu_as_notas(indice):
    local = CrossEncoderReranker("modelo-local", score_fn=_nota_por_palavra("microempresa"))
    reranker = EcoaReranker(url="http://servidor/api/rerank", post_fn=_servidor_fora_do_ar, fallback=local)

    retrieve("microempresa no pregao", {"bases": ["estruturada"]}, top_k=2, reranker=reranker, **indice)

    rerank = next(r for r in _registros() if r["stage"] == "rerank")
    assert rerank["status"] == "degradado"
    assert rerank["extra"]["fallback"] == "reranker_local"
    assert rerank["extra"]["rerank_backend"].startswith("local:modelo-local")


def test_pre_carregar_carrega_o_modelo_configurado(monkeypatch):
    from src import rerank
    carregados = []
    monkeypatch.setattr(config, "RERANK_ENABLED", True)
    monkeypatch.setattr(rerank, "_carregar_cross_encoder", lambda nome, tamanho: carregados.append((nome, tamanho)))

    assert rerank.pre_carregar() is True
    assert carregados == [(config.RERANK_MODEL, config.RERANK_MAX_LENGTH)]


def test_pre_carregar_nao_faz_nada_com_rerank_desligado(monkeypatch):
    from src import rerank
    monkeypatch.setattr(config, "RERANK_ENABLED", False)
    monkeypatch.setattr(rerank, "_carregar_cross_encoder", lambda *_: pytest.fail("não deveria carregar"))

    assert rerank.pre_carregar() is False


def test_pre_carregar_que_falha_nao_derruba_a_api(monkeypatch):
    from src import rerank

    def falha(*_):
        raise OSError("sem rede para baixar o modelo")

    monkeypatch.setattr(config, "RERANK_ENABLED", True)
    monkeypatch.setattr(rerank, "_carregar_cross_encoder", falha)

    assert rerank.pre_carregar() is False
