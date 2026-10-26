"""Restrições conservadoras de objeto, sem sinônimos técnicos ou IDs do golden.

Uma compra explícita de aparelho/máquina não é atendida por um objeto cujo
núcleo é peça, consumível ou serviço. Só rejeitamos incompatibilidades claras
no início do objeto: não classificamos o produto por palavras do fornecedor,
nem descartamos um equipamento porque seu descritivo menciona uma peça.
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from typing import Any, Mapping

# Os filtros de objeto rodam sobre cada registro do recorte (107 mil contratos
# na v7) e, numa pergunta de aparelho, levavam ~100 s: o JSON do contrato era
# lido e o objeto normalizado a cada pergunta. As funções abaixo são puras do
# texto, então o resultado fica guardado; o tamanho cobre o corpus inteiro.
_CACHE_POR_REGISTRO = 1 << 18


def _normalizar(texto: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", texto.casefold())
                            if not unicodedata.combining(c)).split())


_BEM_COMPLETO = re.compile(r"\b(?:aparelhos?|maquinas?|equipamentos?\s+(?:completos?|permanentes?))\b")
_COMPRA = re.compile(r"\b(?:compr\w*|adquir\w*|aquisic\w*|fornec\w*|contrat\w*|atas?|registro de precos)\b")
_AUXILIAR = re.compile(
    r"^(?:pecas?|acessorios?|insumos?|consumiveis?|filmes?|seringas?|tubos?|ampolas?|cartuchos?|toner|tintas?|cabos?|"
    r"manutencao|consertos?|reparos?|servicos?|exames?|procedimentos?|consultoria|locacao|aluguel)$"
)
_ESCOPO_AUXILIAR = re.compile(
    r"\b(?:pecas?|acessorios?|insumos?|filmes?|seringas?|manutencao|consertos?|reparos?|"
    r"servicos?|exames?|procedimentos?|locacao|aluguel|uso|utilizacao|operacao|sem|exceto|nao)\b"
)
_PREFACIO = frozenset("""
    aquisicao compra fornecimento contratacao empresa especializada especializado
    prestacao instalacao registro precos preco futura futuro futuros futuras eventual eventuais
    parcelada parcelado parcelados parceladas continuado continuada
    de do da dos das e para na no nas nos por um uma uns umas o a os as
""".split())


def solicita_bem_completo(query: str) -> bool:
    texto = _normalizar(query)
    if not _COMPRA.search(texto):
        return False
    for match in _BEM_COMPLETO.finditer(texto):
        # 'manutenção de aparelho', 'peças para máquina' e negações não são
        # pedidos de aquisição do bem completo. Na dúvida, não criar filtro.
        if not _ESCOPO_AUXILIAR.search(texto[max(0, match.start() - 100):match.start()]):
            return True
    return False


def radical_lexical(palavra: str) -> str:
    """Flexões nominais transparentes; não expande siglas nem sinônimos técnicos."""
    token = _normalizar(palavra)
    if token.endswith("s") and len(token) > 5:
        token = token[:-1]
    for sufixo in ("ora", "or", "ia", "ao", "o", "a"):
        if token.endswith(sufixo) and len(token) - len(sufixo) >= 5:
            return token[:-len(sufixo)]
    return token


def tema_bem_completo(query: str) -> str | None:
    if not solicita_bem_completo(query):
        return None
    texto = _normalizar(query)
    match = re.search(r"\b(?:aparelhos?|maquinas?)\s+(?:de|para)\s+([a-z]+)\b", texto)
    if not match or len(match[1]) < 5 or match[1] in {
        "atender", "realizar", "executar", "servico", "servicos", "saude", "educacao",
        "laboratorio", "escritorio", "utilizacao", "uso",
    }:
        return None
    return radical_lexical(match[1])


@lru_cache(maxsize=_CACHE_POR_REGISTRO)
def radicais_do_objeto(objeto: str) -> frozenset[str]:
    return frozenset(radical_lexical(t) for t in re.findall(r"[a-z]+", _normalizar(objeto)))


def objeto_do_registro(metadata: Mapping[str, Any], content: str = "") -> str | None:
    extras = metadata.get("extras")
    for fields in (metadata, extras if isinstance(extras, Mapping) else {}):
        if isinstance(fields.get("objeto"), str) and fields["objeto"].strip():
            return fields["objeto"]
    return _objeto_do_conteudo(content)


@lru_cache(maxsize=_CACHE_POR_REGISTRO)
def _objeto_do_conteudo(content: str) -> str | None:
    if content.lstrip().startswith("{"):
        try:
            payload = json.loads(content)
        except ValueError:
            return None
        if isinstance(payload, Mapping):
            for key in ("objetoContrato", "objetoCompra", "objetoContratacao"):
                if isinstance(payload.get(key), str) and payload[key].strip():
                    return payload[key]
        return None
    return content.strip() or None


@lru_cache(maxsize=_CACHE_POR_REGISTRO)
def objeto_auxiliar(objeto: str) -> bool:
    texto = re.sub(r"^(?:\s*\[[^\]]+\]\s*)+", "", _normalizar(objeto))
    tokens = re.findall(r"[a-z]+|\d+", texto)
    while tokens and (tokens[0] in _PREFACIO or tokens[0].isdigit()):
        tokens.pop(0)
    if not tokens:
        return False  # sem indício suficiente para classificar o núcleo
    if tokens[:3] == ["material", "de", "consumo"]:
        return True
    if not _AUXILIAR.fullmatch(tokens[0]):
        return False
    # Um lote misto de peças E aparelhos pode conter o bem pedido. Isso não
    # se aplica a 'manutenção de máquinas e aparelhos', que continua serviço.
    if re.fullmatch(r"pecas?|acessorios?|insumos?", tokens[0]) and re.search(
        r"\be\s+(?:(?:de|dos|das)\s+)?(?:aparelhos?|maquinas?|equipamentos?)\b", texto
    ):
        return False
    return True
