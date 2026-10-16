"""Regras puras de recorte PNCP, compartilhadas por aquisição e staging.

O ano do identificador não substitui assinatura/publicação. Cadastro local de
unidades é evidência observacional, não prova de cobertura estadual completa.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
from typing import Any, Mapping

POLICY_VERSION = 1


def primeira(dados: Mapping[str, Any], *campos: str) -> Any:
    return next((dados[c] for c in campos if dados.get(c) not in (None, "")), None)


def uf_unidade(registro: Mapping[str, Any]) -> str:
    unidade = primeira(registro, "unidadeOrgao", "unidadeExecutora", "unidadeAdministrativa", "unidade") or {}
    if not isinstance(unidade, dict):
        return ""
    uf = primeira(unidade, "ufSigla", "siglaUF")
    local = primeira(unidade, "localidade", "municipio") or {}
    if not uf and isinstance(local, dict):
        uf = primeira(local, "uf", "ufSigla", "siglaUF")
    if isinstance(uf, dict):
        uf = primeira(uf, "siglaUF", "ufSigla", "sigla")
    return str(uf or "").upper()


def chave_unidade(registro: Mapping[str, Any]) -> tuple[str, str]:
    orgao = registro.get("orgaoEntidade") or {}
    unidade = registro.get("unidadeOrgao") or {}
    return (
        str(orgao.get("cnpj") or registro.get("cnpjOrgao") or ""),
        str(primeira(unidade, "codigoUnidade", "codigoUnidadeAdministrativa") or registro.get("codigoUnidadeOrgao") or ""),
    )


def ano_assinatura(registro: Mapping[str, Any]) -> str:
    return str(registro.get("dataAssinatura") or "")[:4]


def nome_instrumento(registro: Mapping[str, Any]) -> str:
    valor = primeira(registro, "tipoContrato", "tipoContratoNome", "tipoContratoDescricao", "tipoInstrumento")
    if isinstance(valor, dict):
        valor = primeira(valor, "nome", "descricao", "tipo")
    return str(valor or "")


def tipo_instrumento(registro: Mapping[str, Any]) -> str:
    nome = nome_instrumento(registro).casefold()
    if "empenho" in nome:
        return "empenho"
    if "contrato" in nome:
        return "contrato_assinado"
    return "outro_instrumento" if nome else "instrumento_nao_informado"


def compra_vinculada(registro: Mapping[str, Any]) -> str:
    return str(primeira(registro, "numeroControlePncpCompra", "numeroControlePNCPCompra") or "")


def periodos(inicio: str, fim: str) -> list[tuple[date, date]]:
    a, b = date.fromisoformat(inicio), date.fromisoformat(fim)
    if a > b:
        raise ValueError("dataInicial posterior à dataFinal")
    resultado = []
    while a <= b:
        ultimo = min(b, a + timedelta(days=364))
        resultado.append((a, ultimo))
        a = ultimo + timedelta(days=1)
    return resultado


@dataclass(frozen=True)
class PoliticaColeta:
    ano: int = 2025
    uf: str = "RJ"
    publicacao_inicio: str = "2025-01-01"
    publicacao_fim: str = "2026-09-25"
    versao: int = POLICY_VERSION

    def __post_init__(self) -> None:
        periodos(self.publicacao_inicio, self.publicacao_fim)
        if self.ano < 2000 or not self.uf:
            raise ValueError("ano/UF de recorte inválidos")

    def decidir(self, registro: Mapping[str, Any], *, uf_resolvida: str = "") -> tuple[bool, str]:
        ata = bool(registro.get("numeroControlePNCPAta"))
        contrato = "-2-" in str(registro.get("numeroControlePNCP") or "")
        if ata:
            if str(registro.get("anoAta")) != str(self.ano):
                return False, "ano_ata_fora_recorte"
        elif contrato:
            if ano_assinatura(registro) != str(self.ano):
                return False, "assinatura_fora_recorte"
        else:
            publicada = str(registro.get("dataPublicacaoPncp") or "")[:10]
            if not publicada:
                return False, "publicacao_nao_informada"
            if not f"{self.ano}-01-01" <= publicada <= f"{self.ano}-12-31":
                return False, "publicacao_fora_recorte"
        uf = uf_unidade(registro) or uf_resolvida
        if not uf:
            return False, "territorio_pendente"
        if uf != self.uf:
            return False, "unidade_fora_recorte"
        # Nas atas a janela é de vigência, não de publicação.
        if contrato:
            publicada = str(registro.get("dataPublicacaoPncp") or "")[:10]
            if not publicada:
                return False, "publicacao_nao_informada"
            if not self.publicacao_inicio <= publicada <= self.publicacao_fim:
                return False, "publicacao_fora_janela"
        return True, "incluido"

    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()
