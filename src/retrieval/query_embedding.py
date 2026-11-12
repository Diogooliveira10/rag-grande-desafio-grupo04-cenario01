"""Embedding da pergunta para a busca densa: instrução Qwen, cache em disco e cooldown.

Separado de src/retrieve.py em 2026-11-12 sem mudar a lógica; os nomes
continuam importáveis de lá, menos ``QUERY_CACHE_PATH``: ele mora só aqui
(quem troca o cache, como o conftest dos testes, troca neste módulo).
"""

from __future__ import annotations

import json
import math
import time
from typing import Callable

import config
from src.obs import TokenUsage, token_usage_from_response
from src.retrieval.records import EmbeddingUnavailable, _read_jsonl
from src.settings import ConfiguracaoEmbeddings


EmbedFn = Callable[[list[str]], list[list[float]]]


QUERY_INSTRUCTION = "Given a public procurement question, retrieve relevant passages that answer the query"


QUERY_CACHE_PATH = config.ROOT_DIR / "data" / "embeddings" / "query_cache.jsonl"


_EMBEDDING_FAILURES: dict[tuple[str, str], float] = {}


def embedding_query_text(query: str) -> str:
    """Instrução Qwen exclusiva da busca densa; BM25/roteamento recebem o original."""
    return f"Instruct: {QUERY_INSTRUCTION}\nQuery:{query}"


def _default_embed(query: str) -> list[float]:
    return _embed_fn_com_uso([])([query])[0]


def _embed_fn_com_uso(uso: list[TokenUsage], estatisticas: dict[str, int] | None = None,
                       observacoes: list[dict] | None = None,
                       embeddings: ConfiguracaoEmbeddings | None = None) -> EmbedFn:
    """Embedding padrão (servidor da turma) que anota o ``usage`` de cada chamada.

    Usado por ``retrieve`` para preencher ``tokens`` do stage
    ``retrieval_denso``; quem passa ``embed_fn`` próprio fica sem contagem.
    Sem ``embeddings``, usa a configuração atual do ``config`` em cada chamada.
    """
    def embed(texts: list[str]) -> list[list[float]]:
        e = embeddings or config.atual().embeddings
        encoded = [embedding_query_text(text) for text in texts]
        endpoint, chave_api, modelo = e.endpoint()
        chave = {"endpoint": endpoint, "model": modelo, "texts": encoded}
        for record in _read_jsonl(QUERY_CACHE_PATH):
            if record.get("key") == chave:
                vectors = record["vectors"]
                if len(vectors) == len(texts) and all(
                    vector and all(math.isfinite(v) for v in vector) and any(vector)
                    for vector in vectors
                ):
                    uso.append(TokenUsage())
                    if estatisticas is not None:
                        estatisticas["cache_hits"] += 1
                    return vectors
        # O cache continua disponível durante a indisponibilidade. Nunca usar
        # outro encoder automaticamente: mesma dimensão não garante compatibilidade.
        endpoint_key = (endpoint, modelo)
        if time.monotonic() < _EMBEDDING_FAILURES.get(endpoint_key, 0):
            raise EmbeddingUnavailable("Embedding em cooldown após falha de disponibilidade")
        # Cliente criado só na chamada: sem candidatos, dense_retrieve nem embeda.
        from openai import OpenAI
        import httpx
        from src.obs import aguardar_modelo

        aguardar_modelo()
        client = OpenAI(base_url=endpoint, api_key=chave_api,
                        timeout=httpx.Timeout(e.query_timeout, connect=min(3.0, e.query_timeout)),
                        max_retries=0)
        from src.model_observation import observar_chamada
        diagnostics: dict = {}
        if estatisticas is not None:
            estatisticas["calls"] += 1
        try:
            response = observar_chamada(lambda: client.embeddings.create(model=modelo, input=encoded),
                provider=e.provider, model=modelo, sdk_max_retries=0, diagnosticos=diagnostics)
        except Exception as error:
            from llm_ecoa import erro_de_disponibilidade
            if erro_de_disponibilidade(error):
                _EMBEDDING_FAILURES[endpoint_key] = time.monotonic() + e.query_cooldown
            raise
        else:
            _EMBEDDING_FAILURES.pop(endpoint_key, None)
        finally:
            if observacoes is not None:
                observacoes.extend(diagnostics.get("chamadas_modelo", []))
        uso.append(token_usage_from_response(response))
        vectors = [item.embedding for item in sorted(response.data, key=lambda item: getattr(item, "index", 0))]
        if len(vectors) != len(texts) or not all(
            vector and all(math.isfinite(v) for v in vector) and any(vector) for vector in vectors
        ):
            raise ValueError(f"{e.provider} retornou embeddings de query inválidos")
        QUERY_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with QUERY_CACHE_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"key": chave, "vectors": vectors}, ensure_ascii=False) + "\n")
        return vectors

    return embed
