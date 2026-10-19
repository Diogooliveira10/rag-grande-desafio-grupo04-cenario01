"""Estágio ``rerank``: um cross-encoder reordena os candidatos da fusão RRF.

A busca densa compara vetores da pergunta e do chunk calculados separadamente,
e a BM25 só conta termos. O cross-encoder lê pergunta e chunk juntos e dá uma
nota de relevância para o par — mais caro, por isso roda só sobre os
``config.RERANK_CANDIDATES`` primeiros do RRF, e o ``retrieve`` corta o
resultado em ``top_k`` depois.

O modelo padrão (``config.RERANK_MODEL``) é um cross-encoder multilíngue
treinado no mMARCO (que inclui português) e roda em CPU. O modelo é carregado
na primeira chamada e fica em cache no processo; o download inicial vai para o
cache do Hugging Face.

Com ``RERANK_PROVIDER=ecoa``, quem dá as notas é o endpoint ``/api/rerank`` do
servidor da turma (``EcoaReranker``): manda a pergunta e os textos e recebe uma
nota por texto. Se o endpoint falhar, o cross-encoder local assume; se também
não houver, o ``retrieve`` segue com a ordem da fusão. ``backend_usado`` diz
qual dos dois deu as notas, e o ``retrieve`` leva isso ao log. O padrão
continua sendo o local: o endpoint usa o mesmo modelo de embedding da busca
densa e, na ablação de 2026-10-07 (``docs/ABLACAO_RERANK.md``), acertou menos.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import replace
from functools import lru_cache
from typing import Any, Callable, Optional, Sequence

import config
from src.retrieve import RetrievalResult

logger = logging.getLogger(__name__)

ScoreFn = Callable[[list[tuple[str, str]]], Sequence[float]]
PostFn = Callable[[str, dict[str, Any], float], dict[str, Any]]

# Teto por texto enviado ao endpoint: os candidatos já vêm resumidos por
# texto_para_rerank, e o corpo da requisição fica pequeno.
_MAX_CHARS_ECOA = 2000

_PAR_JSON_RE = re.compile(r'"(\w+)":\s*(?:"((?:[^"\\]|\\.)*)"|(-?\d+(?:\.\d+)?))')
# Campos que dizem do que o registro trata vêm primeiro; o resto segue na ordem original.
_CAMPOS_PRIORITARIOS = (
    "objeto", "titulo", "descricao", "razaosocial", "nomefornecedor", "nomeunidade", "nomeorgao",
    "nomerazaosocial",
    "modalidadenome", "valor", "municipio", "ufsigla", "situacao", "tipodocumentonome",
)


def texto_para_rerank(conteudo: str) -> str:
    """Texto que o cross-encoder lê para um chunk.

    Os chunks das bases estruturadas são JSON em ordem alfabética de chave: o
    ``objetoCompra`` de um contrato fica perto do token 480, e o modelo só lê
    ``RERANK_MAX_LENGTH`` tokens (pergunta incluída). Para JSON, os pares
    ``chave: valor`` saem com os campos de conteúdo primeiro, sem nulos, URLs e
    códigos; texto comum passa sem mudança.
    """
    if not conteudo.lstrip().startswith("{"):
        return conteudo
    pares = []
    for chave, texto, numero in _PAR_JSON_RE.findall(conteudo):
        valor = texto if texto else numero
        if not valor or valor.startswith("http"):
            continue
        pares.append((chave, valor.replace("\\n", " ").replace("\\t", " ")))
    if not pares:
        return conteudo

    def prioridade(item: tuple[int, tuple[str, str]]) -> tuple[int, int]:
        posicao, (chave, _valor) = item
        chave_normalizada = chave.lower()
        for indice, prefixo in enumerate(_CAMPOS_PRIORITARIOS):
            if chave_normalizada.startswith(prefixo):
                return (indice, posicao)
        return (len(_CAMPOS_PRIORITARIOS), posicao)

    ordenados = sorted(enumerate(pares), key=prioridade)
    # PNCP completo: explicitar os papéis das entidades e reservar espaço para
    # o órgão. Objetos longos e a descrição do amparo legal não podem consumir
    # toda a janela do cross-encoder antes do contexto institucional.
    try:
        registro = json.loads(conteudo)
    except ValueError:
        registro = {}
    if not isinstance(registro, dict):
        registro = {}
    orgao = registro.get("orgaoEntidade") or {}
    unidade = registro.get("unidadeOrgao") or {}
    nome_orgao = orgao.get("razaoSocial") if isinstance(orgao, dict) else None
    nome_orgao = nome_orgao or registro.get("nomeOrgao")
    chave_objeto = next((chave for chave in ("objetoContrato", "objetoCompra", "objetoContratacao")
                         if isinstance(registro.get(chave), str)), None)
    if nome_orgao and chave_objeto:
        objeto = registro[chave_objeto]
        campos = [
            ("Órgão contratante", nome_orgao),
            ("Objeto", objeto[:600]),
            ("Unidade", unidade.get("nomeUnidade") if isinstance(unidade, dict) else None),
            ("Fornecedor", registro.get("nomeRazaoSocialFornecedor")),
            ("Número do contrato", registro.get("numeroContratoEmpenho")),
        ]
        usados = {chave_objeto, "razaoSocial", "nomeOrgao", "nomeUnidade",
                  "nomeRazaoSocialFornecedor", "numeroContratoEmpenho"}
        campos += [(chave, valor) for _posicao, (chave, valor) in ordenados if chave not in usados]
        if len(objeto) > 600:
            campos.append(("Complemento do objeto", objeto[600:]))
        return "; ".join(f"{chave}: {valor}" for chave, valor in campos if valor not in (None, ""))
    return "; ".join(f"{chave}: {valor}" for _posicao, (chave, valor) in ordenados)


@lru_cache(maxsize=2)
def _carregar_cross_encoder(model_name: str, max_length: int) -> Any:
    from sentence_transformers import CrossEncoder

    return CrossEncoder(model_name, max_length=max_length)


class CrossEncoderReranker:
    """Reranker plugável no ``retrieve``: ``reranker(query, resultados)``.

    ``score_fn`` substitui o modelo (testes, ou outro backend); recebe a lista
    de pares ``(pergunta, texto)`` e devolve uma nota por par.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        max_length: int | None = None,
        batch_size: int = 16,
        score_fn: Optional[ScoreFn] = None,
    ) -> None:
        self.modelo = model_name or config.RERANK_MODEL
        self.max_length = max_length or config.RERANK_MAX_LENGTH
        self.batch_size = batch_size
        self._score_fn = score_fn

    def _scores(self, pares: list[tuple[str, str]]) -> list[float]:
        if self._score_fn is not None:
            return [float(nota) for nota in self._score_fn(pares)]
        modelo = _carregar_cross_encoder(self.modelo, self.max_length)
        notas = modelo.predict(pares, batch_size=self.batch_size, show_progress_bar=False)
        return [float(nota) for nota in notas]

    def __call__(self, query: str, resultados: list[RetrievalResult]) -> list[RetrievalResult]:
        if not resultados:
            return []
        notas = self._scores([(query, texto_para_rerank(item.content)) for item in resultados])
        if len(notas) != len(resultados):
            raise ValueError(f"reranker devolveu {len(notas)} notas para {len(resultados)} chunks")
        # score passa a ser a nota do cross-encoder; rrf_score guarda a da fusão.
        reordenados = [
            replace(item, score=nota, rerank_score=nota) for item, nota in zip(resultados, notas)
        ]
        reordenados.sort(key=lambda item: (-item.score, item.chunk_id))
        return reordenados


def _post_json(url: str, corpo: dict[str, Any], timeout: float) -> dict[str, Any]:
    # UTF-8 explícito: o servidor recusa (400) texto acentuado em outra codificação.
    requisicao = urllib.request.Request(
        url,
        data=json.dumps(corpo, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(requisicao, timeout=timeout) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


class EcoaReranker(CrossEncoderReranker):
    """Rerank pelo endpoint ``/api/rerank`` do servidor da turma.

    Corpo: ``{"query", "documents", "top_n"}``; resposta: ``{"model",
    "results": [{"index", "relevance_score"}, ...]}``. Pede nota para todos os
    textos (``top_n`` = quantidade). Com ``fallback``, uma falha do endpoint
    (rede, timeout, resposta fora do formato) passa os mesmos pares a ele.
    """

    def __init__(
        self,
        url: str | None = None,
        *,
        timeout: float | None = None,
        fallback: Optional[CrossEncoderReranker] = None,
        post_fn: Optional[PostFn] = None,
    ) -> None:
        super().__init__()
        self.url = url or config.RERANK_URL
        self.timeout = timeout if timeout is not None else config.RERANK_TIMEOUT
        self.fallback = fallback
        self._post = post_fn or _post_json
        self.modelo = f"ecoa:{self.url.rsplit('/api/', 1)[-1]}"
        self.backend_usado: Optional[str] = None

    def _scores_ecoa(self, pares: list[tuple[str, str]]) -> list[float]:
        documentos = [texto[:_MAX_CHARS_ECOA] for _query, texto in pares]
        resposta = self._post(
            self.url,
            {"query": pares[0][0], "documents": documentos, "top_n": len(documentos)},
            self.timeout,
        )
        notas: dict[int, float] = {}
        for item in resposta.get("results") or []:
            nota = item.get("relevance_score", item.get("score"))
            if isinstance(item.get("index"), int) and nota is not None:
                notas[item["index"]] = float(nota)
        if set(notas) != set(range(len(documentos))):
            raise ValueError(f"/api/rerank devolveu notas para {len(notas)} de {len(documentos)} textos")
        if resposta.get("model"):
            self.modelo = f"ecoa:{resposta['model']}"
        return [notas[indice] for indice in range(len(documentos))]

    def _scores(self, pares: list[tuple[str, str]]) -> list[float]:
        try:
            notas = self._scores_ecoa(pares)
        except Exception as erro:
            if self.fallback is None:
                self.backend_usado = None
                raise
            logger.warning("Rerank do ECOA falhou (%s); usando %s", type(erro).__name__, self.fallback.modelo)
            notas = self.fallback._scores(pares)
            self.backend_usado = f"local:{self.fallback.modelo} (ecoa falhou: {type(erro).__name__})"
            return notas
        self.backend_usado = self.modelo
        return notas


def pre_carregar() -> bool:
    """Carrega o cross-encoder local antes da primeira pergunta.

    Vale para os dois provedores: no ``ecoa`` o modelo local é o fallback.
    Devolve se carregou; falha só vira aviso, porque a primeira pergunta
    tenta de novo pelo caminho normal.
    """
    if not config.RERANK_ENABLED:
        return False
    try:
        _carregar_cross_encoder(config.RERANK_MODEL, config.RERANK_MAX_LENGTH)
    except Exception as erro:  # sem sentence-transformers, sem rede para baixar etc.
        logger.warning("Pré-carregamento do rerank falhou (%s); carrega na primeira pergunta", type(erro).__name__)
        return False
    return True


def reranker_padrao() -> Optional[CrossEncoderReranker]:
    """Reranker usado pelo pipeline, ou ``None`` com ``RERANK_ENABLED=false``."""
    if not config.RERANK_ENABLED:
        return None
    if config.RERANK_PROVIDER == "local":
        return CrossEncoderReranker()
    return EcoaReranker(fallback=CrossEncoderReranker())


__all__ = ["CrossEncoderReranker", "EcoaReranker", "pre_carregar", "reranker_padrao"]
