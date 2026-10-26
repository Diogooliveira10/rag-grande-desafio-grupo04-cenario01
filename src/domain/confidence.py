"""Política de confiança das respostas.

Três regras que a banca costuma perguntar:

1. o recorte do SQL **limita** a confiança, nunca aumenta;
2. uma resposta do LLM que admite falta de dados sai no máximo com confiança média;
3. zero correspondências numa consulta completa continua confiável: sustenta
   "não há registros correspondentes".
"""

from __future__ import annotations

import re
from typing import Any

from src.domain.text import sem_acento
from src.schema import Chunk, ConfidenceLevel, RAGResponse

# Ordem do menor para o maior; a enumeração é str e não compara por mérito.
NIVEIS = {ConfidenceLevel.BAIXA: 1, ConfidenceLevel.MEDIA: 2, ConfidenceLevel.ALTA: 3}

# Frases com que a própria resposta admite falta de dados ou de confirmação.
# Comparadas sem acento e em minúsculas.
RESSALVA_DE_INSUFICIENCIA = re.compile(
    r"insuficien"
    r"|\bnao ha (?:dados|informac\w*|evidencia\w*|amostra)\b[^.]{0,40}\bsuficientes?\b"
    r"|\bnao ha (?:mencao|confirmacao|registro) explicit"
    r"|\bnao (?:menciona|mencionam|confirma|confirmam) explicitamente\b"
    r"|\bnao (?:e possivel|permitem?|ha como) (?:concluir|determinar|afirmar|calcular|confirmar)\b"
    r"|\bfaltam dados\b|\blimitando a conclusao\b"
)
AVISO_RESSALVA = ("Confiança limitada a média: a própria resposta registra falta de dados "
                  "ou de confirmação explícita no acervo.")


def confianca_do_recorte(recorte: dict[str, Any]) -> tuple[ConfidenceLevel, list[str]]:
    """Confiabilidade da afirmação NO RECORTE, sem misturar completude/cobertura.

    Zero correspondências **não** reduz confiança sozinho: uma consulta válida e
    exaustiva sobre o snapshot sustenta "não há registros correspondentes". O
    que limita confiança são execução parcial ou cálculo não verificável — cada
    um pela sua própria razão. Completude e cobertura viram avisos à parte, para
    não parecerem dúvida sobre a conta.
    """
    avisos: list[str] = []
    if not recorte:
        return ConfidenceLevel.MEDIA, avisos

    # Cobertura é declaração de escopo, não mérito da afirmação: vai sempre e
    # não mexe na confiança.
    avisos.append("Escopo: amostra do PNCP/RJ registrada neste snapshot, não o "
                  "universo de contratações do Estado.")
    if recorte.get("snapshot_versao"):
        avisos.append(f"Snapshot: esquema v{recorte['snapshot_versao']}, "
                      f"{recorte.get('registros_no_snapshot', 0)} registros.")

    # Completude: campos sem valor conhecido ficam fora da conclusão, à mostra.
    for campo in recorte.get("completude") or []:
        if campo.get("desconhecidos"):
            avisos.append(f"Completude: {campo['campo']} desconhecido em "
                          f"{campo['desconhecidos']} de {campo['conhecidos'] + campo['desconhecidos']} "
                          "registros do recorte; a conclusão vale para os que têm o campo.")

    if recorte.get("execucao") == "parcial":
        motivos = recorte.get("filtros_relaxados") or ["filtros relaxados"]
        avisos.append("Execução parcial (" + "; ".join(motivos) + "): a conta não cobre "
                      "exatamente o que foi perguntado.")
        return ConfidenceLevel.MEDIA, avisos
    if recorte.get("tipo_resultado") == "comparacao_insuficiente":
        avisos.append(recorte.get("calculo_motivo")
                      or "Comparação entre esferas sem dados suficientes: menos de duas têm valor informativo.")
        return ConfidenceLevel.BAIXA, avisos
    if recorte.get("tipo_resultado") == "calculo_indisponivel":
        avisos.append(recorte.get("calculo_motivo")
                      or "Cálculo indisponível: não há campo de valor conhecido no recorte.")
        return ConfidenceLevel.BAIXA, avisos

    # Registros, soma zero ou zero correspondências, todos com execução
    # completa: confiáveis no recorte.
    return ConfidenceLevel.ALTA, avisos


def confianca_dos_recortes(chunks: list[Chunk]) -> tuple[ConfidenceLevel, list[str]]:
    """O menor nível entre os recortes SQL dos chunks, com os avisos de todos."""
    recortes = [c.metadata.extras.get("resultado_sql", {}).get("recorte") for c in chunks
                if isinstance(c.metadata.extras.get("resultado_sql"), dict)]
    resultados = [confianca_do_recorte(r) for r in recortes if r]
    if not resultados:
        return ConfidenceLevel.MEDIA, []
    return min((n for n, _ in resultados), key=lambda n: NIVEIS[n]), list(dict.fromkeys(
        aviso for _, avisos in resultados for aviso in avisos
    ))


def recorte_dos_chunks(chunks: list[Chunk]) -> dict[str, Any]:
    for chunk in chunks:
        resultado = chunk.metadata.extras.get("resultado_sql")
        if isinstance(resultado, dict) and resultado.get("recorte"):
            return resultado["recorte"]
    return {}


def limitar_confianca_pela_ressalva(resposta: RAGResponse) -> RAGResponse:
    """Resposta do LLM que admite falta de dados não sai com confiança alta.

    O LLM declarava "alta" escrevendo "não há dados suficientes" (P4.2, P3.2,
    P6.1 do golden set). Vale só para o caminho do LLM: a resposta determinística
    do SQL tem a confiança do recorte e não passa por aqui.
    """
    if (resposta.is_refusal or resposta.confidence_level != ConfidenceLevel.ALTA
            or not RESSALVA_DE_INSUFICIENCIA.search(sem_acento(resposta.answer))):
        return resposta
    return resposta.model_copy(update={
        "confidence_level": ConfidenceLevel.MEDIA,
        "avisos": list(dict.fromkeys([*resposta.avisos, AVISO_RESSALVA])),
    })


def aplicar_recorte(resposta: RAGResponse, chunks: list[Chunk]) -> RAGResponse:
    """Mesma política de confiança da resposta determinística, no caminho do LLM.

    O recorte **limita** o nível por razão própria (execução parcial, cálculo não
    verificável), mas não eleva a confiança declarada pelo LLM. Respostas
    determinísticas usam diretamente a confiança do recorte. Recusa não ganha
    confiança e o texto do LLM não é alterado.
    """
    if resposta.is_refusal:
        return resposta
    recorte = recorte_dos_chunks(chunks)
    if not recorte:
        return resposta
    nivel_recorte, avisos = confianca_dos_recortes(chunks)
    atual = resposta.confidence_level
    if atual == ConfidenceLevel.RECUSADO:
        return resposta
    nivel = min(atual, nivel_recorte, key=lambda n: NIVEIS.get(n, 0))
    return resposta.model_copy(update={
        "confidence_level": nivel,
        "avisos": list(dict.fromkeys([*resposta.avisos, *avisos])),
    })
