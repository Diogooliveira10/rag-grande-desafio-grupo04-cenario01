"""Identidades de fornecedores do snapshot, sem confundir nome com objeto.

O catálogo mantém todas as grafias para auditoria. Reconhecimento e filtros
agrupam apenas CNPJs de 14 dígitos pelo radical de oito; CPF, identificadores
estrangeiros e nomes sem documento não são fundidos por aproximação textual.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
from typing import Any

from src.domain.text import normalizar


# Marcas que não são a razão social, ou cujo filtro legado usa trecho comum.
# CEDAE é deliberadamente ausente: a sigla já identifica o órgão contratante.
APELIDOS_CONHECIDOS = {
    "removip": "REMOVIP SERVICO MEDICO LTDA",
    "dataprev": "EMPRESA DE TECNOLOGIA E INFORMACOES DA PREVIDENCIA S.A. - DATAPREV",
    "fiotec": "FIOTEC - FUNDACAO PARA O DESENVOLVIMENTO CIENTIFICO E TECNOLOGICO EM SAUDE",
    "embratel": "EMBRATEL TVSAT TELECOMUNICACOES SA",
    "light": "LIGHT SERVICOS DE ELETRICIDADE S",
    "correios": "EMPRESA BRASILEIRA DE CORREIOS E TEL",
}


def raiz_cnpj(valor: Any) -> str | None:
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos[:8] if len(digitos) == 14 and digitos[:8] != "00000000" else None


def tokens(texto: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", normalizar(texto)))


def compor_fornecedores(banco: Any, genericas: set[str] | frozenset[str],
                        nomes_orgaos: list[str], municipios: list[str]) -> dict[str, Any]:
    grupos: dict[str, dict[str, Any]] = {}
    identidades: dict[str, set[str]] = defaultdict(set)
    frequencias: dict[str, Counter] = defaultdict(Counter)
    linhas = banco.consultar(
        "SELECT fornecedor, fornecedor_cnpj, COUNT(*) AS n FROM contratacoes "
        "WHERE fornecedor IS NOT NULL GROUP BY fornecedor, fornecedor_cnpj")
    for linha in linhas:
        nome = " ".join(str(linha["fornecedor"]).split())
        if not nome:
            continue
        raiz = raiz_cnpj(linha["fornecedor_cnpj"])
        identidade = raiz or "nome:" + normalizar(nome)
        grupo = grupos.setdefault(identidade, {"nomes": set(), "cnpjs": set()})
        grupo["nomes"].add(nome)
        if raiz:
            grupo["cnpjs"].add(re.sub(r"\D", "", str(linha["fornecedor_cnpj"])))
        frequencias[identidade][nome] += linha["n"]
        for token in tokens(nome):
            identidades[token].add(identidade)
    # Estatística congelada junto do catálogo; não depende de qual banco o
    # runtime está usando, nem precisa varrer 100 mil objetos por pergunta.
    palavras: dict[str, list[int]] = {}
    for linha in banco.consultar("SELECT objeto_normalizado AS o, fornecedor AS f FROM contratacoes"):
        do_nome = tokens(linha.get("f") or "")
        for palavra in set(re.findall(r"[a-z]{4,}", linha.get("o") or "")):
            par = palavras.setdefault(palavra, [0, 0])
            par[0] += 1
            par[1] += int(palavra in do_nome)
    proibidas = set(genericas) | {"cedae"}
    proibidas.update(t for nome in nomes_orgaos + municipios for t in tokens(nome))
    canonicos = {key: sorted(frequencias[key], key=lambda n: (-frequencias[key][n], n))[0]
                 for key in grupos}
    apelidos: dict[str, str] = {}
    for palavra, ids in sorted(identidades.items()):
        total, proprio = palavras.get(palavra, (0, 0))
        if (len(ids) == 1 and len(palavra) >= 5 and palavra.isalpha()
                and palavra not in proibidas and (not total or proprio / total >= .8)):
            apelidos[palavra] = canonicos[next(iter(ids))]
    for apelido, trecho in APELIDOS_CONHECIDOS.items():
        ids = {key for key, grupo in grupos.items()
               if any(normalizar(trecho) in normalizar(nome) for nome in grupo["nomes"])}
        if len(ids) == 1:
            apelidos[apelido] = canonicos[next(iter(ids))]
    return {
        "fornecedores_identidades": {
            key: {"nome": canonicos[key], "nomes": sorted(grupo["nomes"]),
                  "cnpjs": sorted(grupo["cnpjs"])} for key, grupo in sorted(grupos.items())
        },
        "apelidos_fornecedor": dict(sorted(apelidos.items())),
        "palavras_objetos": dict(sorted(palavras.items())),
    }


# Forma jurídica no fim da razão social, com ou sem pontuação ("S.A", "S/A", "LTDA.").
_FORMA_JURIDICA = re.compile(r"(?:\s+(?:s\s?a|ltda|me|epp|eireli))+$")


def chave_de_razao_social(nome: str) -> str:
    """Razão social sem acento, pontuação e forma jurídica: "GENTE SEGURADORA S.A" -> "gente seguradora"."""
    texto = " ".join(re.sub(r"[^a-z0-9]+", " ", normalizar(nome)).split())
    return _FORMA_JURIDICA.sub("", texto).strip()


def mesma_razao_social(nome: str, procurado: str, *, exata: bool = False) -> bool:
    """``procurado`` aparece em ``nome`` (ou é ``nome``, com ``exata``), palavra por
    palavra, ignorando pontuação e forma jurídica.

    Reserva do filtro de fornecedor quando o registro não tem CNPJ e a grafia dele
    não está no catálogo: 48 mil contratos dos vetores da v7 só têm o nome, e
    "GENTE SEGURADORA S.A" não casava com "GENTE SEGURADORA SA" (I04).
    """
    chave, buscada = chave_de_razao_social(nome), chave_de_razao_social(procurado)
    if not buscada:
        return False
    return chave == buscada if exata else f" {buscada} " in f" {chave} "


def raizes_do_fornecedor(nome: str) -> tuple[str, ...]:
    from src.analysis.entities import _catalogo_de_orgaos

    catalogo = _catalogo_de_orgaos()
    indice = catalogo.indice_fornecedores if catalogo else None
    return indice.raizes(nome) if indice is not None else ()


class IndiceFornecedores:
    """Índice invertido: variantes do mesmo radical não criam ambiguidade."""

    def __init__(self, dados: dict[str, Any]):
        self.grupos = dados.get("fornecedores_identidades", {})
        self.palavras: dict[str, set[str]] = defaultdict(set)
        self.por_nome: dict[str, set[str]] = defaultdict(set)
        for key, grupo in self.grupos.items():
            for nome in grupo["nomes"]:
                self.por_nome[normalizar(nome)].add(key)
                for token in tokens(nome):
                    self.palavras[token].add(key)
        for apelido, nome in dados.get("apelidos_fornecedor", {}).items():
            self.por_nome[normalizar(apelido)].update(self.por_nome.get(normalizar(nome), ()))
        for apelido, trecho in APELIDOS_CONHECIDOS.items():
            self.por_nome[normalizar(trecho)].update(self.por_nome.get(apelido, ()))

    def valor(self, palavras: list[str]) -> str | None:
        if not palavras:
            return None
        candidatos = set.intersection(*(self.palavras.get(p, set()) for p in palavras))
        return self.grupos[next(iter(candidatos))]["nome"] if len(candidatos) == 1 else None

    def raizes(self, nome: str) -> tuple[str, ...]:
        ids = self.por_nome.get(normalizar(nome), set())
        # Nome homônimo entre empresas distintas não autoriza unir seus filtros.
        return tuple(ids) if len(ids) == 1 and not next(iter(ids)).startswith("nome:") else ()
