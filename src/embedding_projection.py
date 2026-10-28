'''Projeção compacta dos embeddings de documento (§4/§7 do plano v7).

O cenário 1 define a busca semântica "sobre o objeto do contrato"
(``grande-desafio.txt``, persona Rodrigo) e o contraste exigido pelo cenário
preserva-se: identificadores vivem no BM25 (chunk completo) e a semântica vive
no vetor. A projeção muda **somente** o texto enviado ao encoder de documento
— o chunk armazenado (payload do índice, BM25, evidência do LLM) continua
completo.

- ``completa`` (padrão): o texto do chunk inteiro, como na v6;
- ``compacta``: só os campos de descrição (objeto/descrição/título);
- ``enriquecida``: descrição + contexto de identidade (órgão, unidade,
  município, UF, modalidade, fornecedor, categoria/tipo) — o que a completa
  tem de útil sem o ruído de sintaxe do JSON. Identificadores, valores e datas
  ficam de fora: são do BM25 e da consulta estruturada.

O encoder de **consulta** não é projetado — a pergunta já é natural. A
comparação medida entre os dois modos é a condição de promoção da §7
(``eval/medir_projecao.py``).
'''

from __future__ import annotations

import json
from typing import Any, Callable

EmbedFn = Callable[[list[str]], list[list[float]]]

MODO_COMPLETA = "completa"
MODO_COMPACTA = "compacta"
MODO_ENRIQUECIDA = "enriquecida"
MODOS = (MODO_COMPLETA, MODO_COMPACTA, MODO_ENRIQUECIDA)

# Ordem determinística; só campos de descrição, nunca identificador nem valor.
CAMPOS_DE_DESCRICAO = (
    "objetoContrato",
    "objetoCompra",
    "objetoContratacao",
    "objeto",
    "descricao",
    "descricaoResumida",
    "titulo",
)

# Contexto de identidade: o que a projeção completa traz de útil além do
# objeto (órgão/município/modalidade/fornecedor respondem perguntas como
# "ata da prefeitura de X" e "contratos da CRISTALIA"). Caminhos aninhados.
CAMPOS_DE_CONTEXTO = (
    "orgaoEntidade.razaoSocial",
    "nomeOrgao",
    "razaoSocial",
    "unidadeOrgao.nomeUnidade",
    "unidadeOrgao.municipioNome",
    "municipio",
    "unidadeOrgao.ufSigla",
    "uf",
    "modalidadeNome",
    "modalidade",
    "nomeRazaoSocialFornecedor",
    "categoriaProcesso",
    "tipoContrato",
)

_CAMPOS_POR_MODO = {
    MODO_COMPACTA: tuple((campo,) for campo in CAMPOS_DE_DESCRICAO),
    MODO_ENRIQUECIDA: tuple(
        (campo,) for campo in CAMPOS_DE_DESCRICAO + CAMPOS_DE_CONTEXTO
    ),
}


def _valor_de_caminho(registro: dict[str, Any], caminho: str) -> Any:
    atual: Any = registro
    for parte in caminho.split("."):
        if not isinstance(atual, dict):
            return None
        atual = atual.get(parte)
    return atual


def texto_projecao(texto: str, modo: str = MODO_COMPLETA) -> str:
    '''Texto enviado ao encoder de documento no modo pedido.

    Sem campos utilizáveis, cai para o texto completo — um registro sem
    descrição nunca vira vetor vazio em silêncio. Valores repetidos
    (órgão que também é o nome da unidade) aparecem uma vez só.
    '''
    if modo not in _CAMPOS_POR_MODO:
        return texto
    try:
        registro = json.loads(texto)
    except (ValueError, TypeError):
        return texto
    if not isinstance(registro, dict):
        return texto
    partes: list[str] = []
    vistas: set[str] = set()
    for (caminho,) in _CAMPOS_POR_MODO[modo]:
        valor = _valor_de_caminho(registro, caminho)
        if not isinstance(valor, str) or not valor.strip():
            continue
        normalizado = " ".join(valor.split())
        if normalizado.casefold() not in vistas:
            vistas.add(normalizado.casefold())
            partes.append(normalizado)
    return " | ".join(partes) if partes else texto


def embed_fn_concorrente(embed_fn: EmbedFn, n: int = 4) -> EmbedFn:
    '''Divide cada lote em n pedaços e embeda em paralelo.

    Utilitário de execução, semântica inalterada: o servidor local atende n
    slots (``llama-server --parallel n``) e o ganho é ~n×. A ordem dos vetores
    devolvidos é a dos textos recebidos.
    '''
    if n <= 1:
        return embed_fn

    def embed(texts: list[str]) -> list[list[float]]:
        if len(texts) <= 1:
            return embed_fn(texts)
        from concurrent.futures import ThreadPoolExecutor

        pedacos = [texts[i::n] for i in range(n)]
        with ThreadPoolExecutor(max_workers=n) as pool:
            resultados = list(pool.map(embed_fn, pedacos))
        saida: list[Any] = [None] * len(texts)
        for i, vetores in enumerate(resultados):
            for posicao, vetor in zip(range(i, len(texts), n), vetores):
                saida[posicao] = vetor
        return saida

    encoding_config = getattr(embed_fn, "encoding_config", None)
    if encoding_config is not None:
        embed.encoding_config = encoding_config  # type: ignore[attr-defined]
    return embed


def embed_fn_com_pausa(embed_fn: EmbedFn, pausa_s: float) -> EmbedFn:
    '''Espera ``pausa_s`` depois de cada lote — respeita endpoint compartilhado.'''
    import time

    def embed(texts: list[str]) -> list[list[float]]:
        vetores = embed_fn(texts)
        if pausa_s > 0:
            time.sleep(pausa_s)
        return vetores

    return embed


def embed_fn_com_fallback(
    primario: EmbedFn,
    reserva: EmbedFn,
    *,
    estatisticas: dict[str, int] | None = None,
    ao_fallback=None,
) -> EmbedFn:
    '''Tenta o primário lote a lote; na falha, usa a reserva e registra.

    Os dois encoders devem servir o MESMO modelo base — o fallback troca a
    origem do vetor, não o espaço vetorial. ``estatisticas`` acumula
    ``{"primario": n, "reserva": n}`` (vetores por origem) e
    ``ao_fallback(erro, tamanho_do_lote)`` é chamado a cada troca, para o
    inventário registrar quando o build rodou degradado.
    '''

    def embed(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vetores = primario(texts)
        except Exception as erro:  # falha do provedor: o build continua
            if estatisticas is not None:
                estatisticas["reserva"] = estatisticas.get("reserva", 0) + len(texts)
            if ao_fallback is not None:
                ao_fallback(erro, len(texts))
            return reserva(texts)
        if estatisticas is not None:
            estatisticas["primario"] = estatisticas.get("primario", 0) + len(vetores)
        return vetores

    return embed


def embed_fn_projetado(embed_fn: EmbedFn, modo: str = MODO_COMPLETA) -> EmbedFn:
    '''Envolve ``embed_fn`` projetando os textos de documento no modo pedido.

    ``encoding_config`` (quando o encoder expõe) é preservado — o
    ``src/index.py`` o registra no manifest do índice.
    '''
    if modo not in MODOS:
        raise ValueError(f"modo de projeção desconhecido: {modo!r}")
    if modo == MODO_COMPLETA:
        return embed_fn

    def embed(texts: list[str]) -> list[list[float]]:
        return embed_fn([texto_projecao(texto, modo) for texto in texts])

    encoding_config = getattr(embed_fn, "encoding_config", None)
    if encoding_config is not None:
        embed.encoding_config = encoding_config  # type: ignore[attr-defined]
    return embed
