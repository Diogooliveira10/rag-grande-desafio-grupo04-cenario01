r"""Roda o golden set de perguntas de referência contra o pipeline e mede regressão.

Uso:
    py .\eval\run_eval.py check
    py .\eval\run_eval.py run --pipeline eval.adapters:rota_apenas
    py .\eval\run_eval.py run --pipeline src.pipeline:responder
    py .\eval\run_eval.py run --pipeline eval.adapters:pipeline_completo
    py .\eval\run_eval.py run --pipeline eval.adapters:pipeline_completo --ids P1.1 G1

``check`` só valida o golden set (contagem por tipo), sem chamar nenhum
pipeline. ``run`` importa a função apontada por ``--pipeline`` (formato
``modulo.submodulo:funcao``), roda cada pergunta do golden set contra ela e
grava o relatório (JSON + Markdown) em ``eval/runs/<run_id>/``.
``eval.adapters:rota_apenas`` avalia só o roteamento (``src/router.py``) —
rápido e offline. ``src.pipeline:responder`` roda o pipeline completo
(roteamento + recuperação híbrida + geração), chamando o LLM de verdade;
``eval.adapters:pipeline_completo`` faz o mesmo e ainda junta, a partir dos
logs, os tokens e a latência de cada etapa. ``--ids`` roda só alguns casos.

O relatório separa rota, recusa, conteúdo, presença de citação e fonte esperada.
Para uma resposta completa, ``acertou`` exige todos os critérios aplicáveis.
Adaptadores de rota sem resposta continuam reportando somente o escopo de
roteamento, sem serem apresentados como avaliação factual.

O golden set (``eval/golden_set.jsonl``) é derivado das 18 perguntas de
referência + a pergunta de falha esperada do ``PERSONAS.md``, mais os casos
``G*`` de guardrail do cenário (dados bancários de fornecedor, credenciais em
e-mail, juízo sobre contrato específico) e os ``E*`` dos e-mails sintéticos
(um e-mail público, duas recusas de conteúdo restrito). Cada caso
declara a rota esperada (bases) e, quando aplicável, se a resposta correta é
uma recusa — é o gabarito citado em 2.3 do documento do desafio: rodar esse
benchmark antes e depois de atualizar a base mostra se a recuperação piorou.

Pergunta que continua a anterior ("Esses contratos…") declara em
``depende_de`` os casos que vêm antes dela na conversa da persona. O avaliador
manda essas perguntas como ``historico`` ao adaptador que o aceita
(``pipeline_completo`` e ``src.pipeline:responder``), como o frontend faz.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import os
import re
import statistics
import sys
import threading
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

PipelineFn = Callable[[str], dict[str, Any]]

DEFAULT_GOLDEN_SET = PROJECT_ROOT / "eval" / "golden_set.jsonl"
DEFAULT_RUNS_DIR = PROJECT_ROOT / "eval" / "runs"

# Valor de ``refusal_reason`` que ``src/generate.py`` devolve quando o provedor do
# LLM falha; a avaliação o trata como erro de infraestrutura, não como recusa.
ERRO_PROVEDOR = "erro_provedor"

_SOURCE_BASE_ALIASES = {
    "normativa": "editais_normativo",
}


@dataclass(frozen=True)
class EvalCase:
    """Um caso do golden set, derivado de uma pergunta de referência do PERSONAS.md."""

    id: str
    persona: str
    titulo: str
    pergunta: str
    tipo: str
    bases_esperadas: list[str]
    is_refusal_esperado: bool
    refusal_reason_esperado: Optional[str]
    e_pergunta_de_falha_esperada: bool
    hipotese_falha: Optional[str] = None
    comportamento_esperado: Optional[str] = None
    deve_conter: list[str] = field(default_factory=list)
    nao_deve_conter: list[str] = field(default_factory=list)
    # Cada grupo é uma alternativa: basta UM dos sinônimos aparecer. Não se deve
    # exigir todas as frases equivalentes ao mesmo tempo.
    deve_conter_qualquer: list[list[str]] = field(default_factory=list)
    # Critérios numéricos tipados, com tolerância por caso e valor já canônico.
    criterios_numericos: list["CriterioNumerico"] = field(default_factory=list)
    fontes_esperadas_prefixo: list[str] = field(default_factory=list)
    confianca_maxima: Optional[str] = None
    # Casos que vêm antes deste na conversa da persona. O ``historico`` leva só
    # as perguntas deles: a resposta mudaria a cada rodada e levaria o erro de
    # um caso para o seguinte.
    depende_de: list[str] = field(default_factory=list)
    historico: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class CriterioNumerico:
    """Um número que a resposta precisa trazer, comparado em valor não em texto.

    ``esperado`` é o valor canônico (``1196949440.00``), nunca a string
    ``R$ 1.196.949.440,00``: a comparação normaliza monetário via
    ``src.retrieve._parse_number``. A tolerância é por caso, absoluta e/ou
    relativa, e vale a mais permissiva das duas.
    """

    campo: str
    esperado: float
    unidade: str = "BRL"
    tolerancia: Optional[float] = None
    tolerancia_relativa: float = 0.0
    rotulo: Optional[str] = None


def load_golden_set(path: Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            cases.append(
                EvalCase(
                    id=record["id"],
                    persona=record["persona"],
                    titulo=record["titulo"],
                    pergunta=record["pergunta"],
                    tipo=record["tipo"],
                    bases_esperadas=list(record.get("bases_esperadas", [])),
                    is_refusal_esperado=bool(record.get("is_refusal_esperado", False)),
                    refusal_reason_esperado=record.get("refusal_reason_esperado"),
                    e_pergunta_de_falha_esperada=bool(record.get("e_pergunta_de_falha_esperada", False)),
                    hipotese_falha=record.get("hipotese_falha"),
                    comportamento_esperado=record.get("comportamento_esperado"),
                    deve_conter=list(record.get("deve_conter") or []),
                    nao_deve_conter=list(record.get("nao_deve_conter") or []),
                    deve_conter_qualquer=[list(g) for g in (record.get("deve_conter_qualquer") or [])],
                    criterios_numericos=[
                        CriterioNumerico(
                            campo=c["campo"], esperado=float(c["esperado"]),
                            unidade=c.get("unidade", "BRL"),
                            tolerancia=(None if c.get("tolerancia") is None else float(c["tolerancia"])),
                            tolerancia_relativa=float(c.get("tolerancia_relativa", 0.0)),
                            rotulo=c.get("rotulo"),
                        )
                        for c in (record.get("criterios_numericos") or [])
                    ],
                    fontes_esperadas_prefixo=list(record.get("fontes_esperadas_prefixo") or []),
                    confianca_maxima=record.get("confianca_maxima"),
                    depende_de=list(record.get("depende_de") or []),
                )
            )
    ids = [case.id for case in cases]
    duplicated = sorted({item for item in ids if ids.count(item) > 1})
    if duplicated:
        raise ValueError(f"IDs duplicados no golden set: {duplicated}")
    return _com_historico(cases)


def _com_historico(cases: list[EvalCase]) -> list[EvalCase]:
    """Resolve ``depende_de`` no carregamento: assim ``--ids P4.2`` roda com o
    histórico certo mesmo sem rodar a P4.1."""
    anteriores: dict[str, EvalCase] = {}
    resolvidos = []
    for case in cases:
        faltando = [caso_id for caso_id in case.depende_de if caso_id not in anteriores]
        if faltando:
            raise ValueError(f"{case.id} depende de caso inexistente ou posterior: {faltando}")
        historico = [{"pergunta": anteriores[caso_id].pergunta} for caso_id in case.depende_de]
        case = replace(case, historico=historico) if historico else case
        anteriores[case.id] = case
        resolvidos.append(case)
    return resolvidos


def _normalizar_texto(valor: Any) -> str:
    texto = unicodedata.normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")
    return " ".join(texto.lower().split())


def _contains_content_term(term: str, text: str) -> bool:
    term_norm = _normalizar_texto(term)
    text_norm = _normalizar_texto(text)
    if re.fullmatch(r"[a-z0-9]+", term_norm):
        return re.search(rf"(?<!\w){re.escape(term_norm)}(?!\w)", text_norm) is not None
    return term_norm in text_norm


# Palavras que negam o que vem depois: "não é tecnologia da informação" não
# afirma "tecnologia da informação". Mesma ideia da janela de negação de
# src/query_analysis, aqui aplicada à RESPOSTA e não à pergunta.
_NEGACAO_RE = re.compile(r"\b(?:n[aã]o|nunca|jamais|nem|diferente|distinto|improcedente)\b")
# Negadores do lado do TERMO: termos como "não há dados suficientes" ou
# "modalidade não informada" afirmam justamente uma negação.
_NEGACAO_TERMO_RE = re.compile(
    r"\b(?:n[aã]o|nunca|jamais|nem|sem|nenhum|nenhuma|nada)\b"
)
# "não se trata de", "não significa", "não corresponde a": a negação pode estar
# mais longe quando vem com verbo de ligação.
_NEGACAO_LONGA_RE = re.compile(
    r"\bn[aã]o\s+(?:\w+\s+){0,2}(?:e|se\s+trata|significa|quer\s+dizer|corresponde|refere|confundir)\b",
    re.IGNORECASE,
)
_JANELA_DE_NEGACAO = 6  # palavras antes do termo
# Vírgula seguida de gerúndio ou conector abre outra oração: em "o trecho não
# identifica a modalidade, tornando insuficiente a amostra", o "não" é da
# primeira oração e a insuficiência está afirmada (golden de 2026-10-23, P3.2).
_NOVA_ORACAO_RE = re.compile(
    # "portanto", "logo" e "assim" ficam de fora: costumam vir intercalados
    # ("não é, portanto, insuficiente" continua negado).
    r",\s+(?=(?:\w+ndo|mas|porem|contudo|entretanto|todavia|o que)\b)"
)


def _termo_afirmado(term: str, text: str) -> bool:
    """True quando ``term`` aparece no texto **sem** negação à esquerda.

    Serve ao ``nao_deve_conter``: pune quem afirma "é tecnologia da informação",
    mas não quem esclarece "não é tecnologia da informação". Sem isso, o gabarito
    puniria a retificação correta, que é justamente o que queremos ver.

    Termos que já afirmam uma negação ("não há dados suficientes", "modalidade
    não consta") não sofrem o veto externo: o fato que eles afirmam é a própria
    ausência, e uma negação vizinha ("a modalidade não consta ..., não há dados
    suficientes") não cancela o reconhecimento da lacuna — reprovar isso puniria
    a resposta certa (defeito #17).
    """
    term_norm = _normalizar_texto(term)
    text_norm = _normalizar_texto(text)
    if not term_norm:
        return False
    nega_o_fato = bool(_NEGACAO_TERMO_RE.search(term_norm))
    if re.fullmatch(r"[a-z0-9]+", term_norm):
        # O plural simples em português não pode reprovar a resposta certa:
        # "microempresa" precisa casar "microempresas". Siglas continuam exatas
        # ("TI" não vira "tis") e termos curtos também, para não casar ruído.
        sufixo = r"s?" if len(term_norm) >= 4 and not term.isupper() else ""
        padrao = rf"(?<!\w){re.escape(term_norm)}{sufixo}(?!\w)"
    else:
        padrao = re.escape(term_norm)
    for achado in re.finditer(padrao, text_norm):
        if nega_o_fato:
            return True
        antes = text_norm[:achado.start()]
        # A negação não atravessa fronteira de frase. Sem isso, "Campos ausentes
        # não são zero. Nenhum fornecedor tem 3" seria lido como negado — o "não"
        # pertence à frase anterior. Reproduzido no teste de regressão. O "|"
        # separa os campos de um registro SQL: em "modalidade: não informada |
        # fornecedor: X", o "não" é da modalidade, não do fornecedor.
        fim_de_frase = max(antes.rfind("."), antes.rfind(";"), antes.rfind("?"),
                           antes.rfind("!"), antes.rfind("\n"), antes.rfind("|"))
        if fim_de_frase >= 0:
            antes = antes[fim_de_frase + 1:]
        oracoes = list(_NOVA_ORACAO_RE.finditer(antes))
        if oracoes:
            antes = antes[oracoes[-1].end():]
        janela = " ".join(antes.split()[-_JANELA_DE_NEGACAO:])
        if not janela:
            return True
        if _NEGACAO_LONGA_RE.search(janela) or _NEGACAO_RE.search(janela):
            continue
        return True
    return False


# --------------------------------------------------------------------------
# Afirmação com cascata: modelo quando há margem, lexical quando não.
#
# O Laya (checkpoint multilíngue) responde "o texto afirma X?" com probabilidade.
# Ele é OPCIONAL e só entra com EVAL_LAYA=on: carregar o modelo custa ~45 s e
# não pode pesar a suíte de testes nem tornar o eval dependente dele.
#
# A cascata cobre o que cada caminho faz de melhor: o modelo resolve paráfrase
# ("cessão de know-how" sem a expressão literal) e o lexical resolve negação de
# forma auditável e determinística. Margem fraca não decide sozinha.
# --------------------------------------------------------------------------

LIMIAR_MARGEM = float(os.getenv("EVAL_LAYA_LIMIAR", "0.25"))
_EVAL_LAYA = os.getenv("EVAL_LAYA", "off").lower()
_agente_laya = None
_trava_laya = threading.Lock()
_QUESTAO_AFIRMA = {
    "afirma": {
        "type": "choice",
        # Pergunta de percepção ("o que o texto diz?"), não de ação.
        "instructions": "O texto afirma que o critério a seguir é verdadeiro?",
        "criteria": {"A": "sim, o texto afirma isso", "B": "não, o texto não afirma isso"},
    }
}


def _carregar_laya():
    """Carrega o checkpoint multilíngue uma vez; None quando indisponível."""
    global _agente_laya
    if _EVAL_LAYA not in {"on", "auto"}:
        return None
    if _agente_laya is not None:
        return _agente_laya
    with _trava_laya:
        if _agente_laya is not None:
            return _agente_laya
        try:
            import laya
            agente = laya.load("convaiinnovations/laya-multilingual")
            agente.predict("aquecimento", _QUESTAO_AFIRMA, lang="pt")  # warm-up
            _agente_laya = agente
        except Exception:
            _agente_laya = False  # não tenta de novo; segue só lexical
            return None
    return _agente_laya or None


def _probabilidade_de_afirmacao(term: str, text: str) -> Optional[float]:
    """P(texto afirma ``term``) pelo Laya, ou None se ele não estiver ativo."""
    agente = _carregar_laya()
    if agente is None:
        return None
    try:
        with _trava_laya:
            res = agente.predict({"texto": text, "criterio": term}, _QUESTAO_AFIRMA, lang="pt")
        return float(res["answers"]["afirma"]["probabilities"].get("A", 0.0))
    except Exception:
        return None


def _afirmacao(term: str, text: str) -> dict[str, Any]:
    """Se a resposta afirma ``term`` — ``None`` quando não dá para decidir.

    Cascata: com margem suficiente o modelo decide (resolve paráfrase); sem
    margem o lexical decide quando **confirma** (auditável, consciente de
    negação). Modelo incerto + lexical negado é caso indecidível aqui:
    paráfrase e negação difícil têm o mesmo lexical e precisam de resultados
    opostos, então não se escolhe — vai para revisão em vez de reprovar.
    """
    prob = _probabilidade_de_afirmacao(term, text)
    if prob is None:
        return {"afirmado": _termo_afirmado(term, text), "origem": "lexical", "probabilidade": None}
    if abs(prob - 0.5) >= LIMIAR_MARGEM:
        return {"afirmado": prob >= 0.5, "origem": "laya", "probabilidade": prob}
    if _termo_afirmado(term, text):
        return {"afirmado": True, "origem": "laya+lexical", "probabilidade": prob}
    return {"afirmado": None, "origem": "incerto", "probabilidade": prob}


def _has_content_criteria(case: EvalCase) -> bool:
    return bool(case.deve_conter or case.nao_deve_conter or case.deve_conter_qualquer
                or case.criterios_numericos or case.confianca_maxima is not None)


# Números como aparecem no texto: monetário com R$, ou numeral com separadores.
_NUMERO_NO_TEXTO_RE = re.compile(r"R\$\s*[+-]?[\d.,]+|[+-]?\d[\d.,]*")


def _numeros_do_texto(texto: str) -> list[Decimal]:
    """Números do texto já normalizados; id/data/formato inválido não vira número."""
    from src.retrieve import _parse_number

    valores: list[Decimal] = []
    for trecho in _NUMERO_NO_TEXTO_RE.findall(texto):
        # O ponto final da frase entra no casamento ([\d.,] é guloso); sem
        # tirá-lo, "R$ 500,00." não viraria número nenhum.
        numero = _parse_number(trecho.rstrip(".,"))
        if numero is not None:
            valores.append(numero)
    return valores


def _numero_confere(criterio: CriterioNumerico, valores: list[Decimal]) -> bool:
    """Algum número da resposta bate com o esperado dentro da tolerância do caso."""
    esperado = Decimal(str(criterio.esperado))
    tolerancia = Decimal("0")
    if criterio.tolerancia is not None:
        tolerancia = max(tolerancia, Decimal(str(criterio.tolerancia)))
    if criterio.tolerancia_relativa:
        tolerancia = max(tolerancia, abs(esperado) * Decimal(str(criterio.tolerancia_relativa)))
    return any(abs(valor - esperado) <= tolerancia for valor in valores)


def source_base_matches(expected_prefix: str, actual_base_id: str) -> bool:
    expected = _normalizar_texto(expected_prefix)
    actual = _normalizar_texto(actual_base_id)
    canonical_expected = _SOURCE_BASE_ALIASES.get(expected)
    canonical_actual = _SOURCE_BASE_ALIASES.get(actual, actual)
    if canonical_expected is not None:
        return canonical_expected == canonical_actual
    return actual.startswith(expected)


def _content_ok(case: EvalCase, response: dict[str, Any]) -> Optional[bool]:
    if "answer" not in response:
        return None
    answer = str(response.get("answer") or "")

    for item in case.deve_conter:
        # Cascata: paráfrase o modelo resolve, negação o lexical segura, e o
        # indecidível não reprova a resposta certa — vira "não avaliado".
        afirmado = _afirmacao(item, answer)["afirmado"]
        if afirmado is None:
            return None
        if not afirmado:
            return False

    # Alternativas equivalentes: basta um sinônimo por grupo.
    for grupo in case.deve_conter_qualquer:
        votos = [_afirmacao(item, answer)["afirmado"] for item in grupo]
        if any(v is True for v in votos):
            continue
        if all(v is None for v in votos):
            return None
        return False

    for item in case.nao_deve_conter:
        # Só a AFIRMAÇÃO é punida; "não é X" é retificação correta e passa.
        if _afirmacao(item, answer)["afirmado"] is True:
            return False

    if case.criterios_numericos:
        valores = _numeros_do_texto(answer)
        for criterio in case.criterios_numericos:
            if not _numero_confere(criterio, valores):
                return False

    if case.confianca_maxima is not None:
        ordem = {"baixa": 0, "media": 1, "alta": 2, "recusado": 0}
        conf_obtida = _normalizar_texto(response.get("confidence_level") or "")
        conf_esperada = _normalizar_texto(case.confianca_maxima)
        if conf_obtida not in ordem or conf_esperada not in ordem:
            return False
        if ordem[conf_obtida] > ordem[conf_esperada]:
            return False

    return True


def _content_validation_status(
    case: EvalCase, response: dict[str, Any], content_ok: Optional[bool]
) -> str:
    if not _has_content_criteria(case):
        return "not_applicable"
    if "answer" not in response or content_ok is None:
        return "not_validatable"
    return "validated" if content_ok else "failed"


def _expected_source_ok(case: EvalCase, response: dict[str, Any]) -> Optional[bool]:
    if not case.fontes_esperadas_prefixo or "answer" not in response:
        return None
    fontes = response.get("sources_used")
    if case.fontes_esperadas_prefixo:
        if not isinstance(fontes, list) or not fontes:
            return False
        prefixes = tuple(_normalizar_texto(p) for p in case.fontes_esperadas_prefixo)
        fonte_ok = False
        for fonte in fontes:
            if isinstance(fonte, dict):
                base_id = fonte.get("base_id")
                nomes = [fonte.get("source_file"), fonte.get("chunk_id")]
            else:
                base_id = None
                nomes = [str(fonte)]
            if base_id is not None and any(
                source_base_matches(prefix, str(base_id))
                for prefix in case.fontes_esperadas_prefixo
            ):
                fonte_ok = True
                break
            for nome in nomes:
                if nome is None:
                    continue
                nome_norm = _normalizar_texto(nome)
                if any(nome_norm.startswith(prefix) for prefix in prefixes):
                    fonte_ok = True
                    break
            if fonte_ok:
                break
        if not fonte_ok:
            return False
    return True


def evaluate_case(case: EvalCase, response: dict[str, Any]) -> dict[str, Any]:
    """Compara a resposta do pipeline com o gabarito de uma pergunta de referência."""
    bases_obtidas = sorted({str(base).lower() for base in response.get("bases_consultadas", []) or []})
    is_refusal_obtido = bool(response.get("is_refusal", False))
    refusal_reason_obtido = response.get("refusal_reason")

    refusal_ok = is_refusal_obtido == case.is_refusal_esperado
    if case.is_refusal_esperado and case.refusal_reason_esperado is not None:
        refusal_ok = refusal_ok and refusal_reason_obtido == case.refusal_reason_esperado

    # Quando a resposta correta é recusar, não cobramos rota: o sistema não deveria escolher base nenhuma.
    rota_ok = True if case.is_refusal_esperado else bases_obtidas == sorted(case.bases_esperadas)

    fontes = response.get("sources_used")
    n_fontes = len(fontes) if isinstance(fontes, list) else None
    if "answer" not in response and "sources_used" not in response:
        citacao_ok = None
    elif not isinstance(fontes, list):
        citacao_ok = False
    else:
        citacao_ok = n_fontes == 0 if is_refusal_obtido else n_fontes > 0

    content_ok = _content_ok(case, response)
    content_validation = _content_validation_status(case, response, content_ok)
    expected_source_ok = _expected_source_ok(case, response)
    escopo_avaliacao = "resposta_completa" if "answer" in response else "roteamento"
    # Um caso de falha esperada tem como resposta correta "não dá para comparar":
    # o que se avalia é o conteúdo (admitir a insuficiência), não a rota. Exigir
    # as bases puniria a resposta honesta, que é justamente a esperada.
    rota_requerida = not case.e_pergunta_de_falha_esperada
    if escopo_avaliacao == "roteamento":
        # Sem conteúdo não há o que avaliar como "admitir insuficiência": a rota
        # continua obrigatória, senão o caso passaria sem demonstrar nada.
        acertou = rota_ok and refusal_ok
    else:
        acertou = (
            (rota_ok or not rota_requerida)
            and refusal_ok
            and content_ok is True
            and citacao_ok is True
            and expected_source_ok is not False
        )

    # Falha do provedor (LLM fora do ar, JSON inválido) não é uma recusa: o
    # sistema não respondeu. Nunca conta como acerto, nem de recusa esperada, e
    # não entra nas métricas de conteúdo e citação, que não foram avaliadas.
    erro_provedor = refusal_reason_obtido == ERRO_PROVEDOR
    if erro_provedor:
        refusal_ok = False
        content_ok = None
        content_validation = _content_validation_status(case, {}, content_ok)
        expected_source_ok = None
        citacao_ok = None
        acertou = False

    return {
        "id": case.id,
        "tipo": case.tipo,
        "bases_esperadas": sorted(case.bases_esperadas),
        "bases_obtidas": bases_obtidas,
        "is_refusal_esperado": case.is_refusal_esperado,
        "is_refusal_obtido": is_refusal_obtido,
        "refusal_reason_esperado": case.refusal_reason_esperado,
        "refusal_reason_obtido": refusal_reason_obtido,
        "rota_ok": rota_ok,
        "rota_requerida": rota_requerida,
        "e_pergunta_de_falha_esperada": case.e_pergunta_de_falha_esperada,
        "refusal_ok": refusal_ok,
        "erro_provedor": erro_provedor,
        "content_ok": content_ok,
        "content_validation": content_validation,
        "expected_source_ok": expected_source_ok,
        "escopo_avaliacao": escopo_avaliacao,
        "acertou": acertou,
        "n_fontes": n_fontes,
        "citacao_ok": citacao_ok,
        "confidence_level": response.get("confidence_level"),
        "answer": response.get("answer"),
        "metricas": response.get("_metricas"),
    }


def _aceita_historico(pipeline_fn: PipelineFn) -> bool:
    """O adaptador recebe ``historico``? O de rota e o da CLI não recebem."""
    try:
        parametros = inspect.signature(pipeline_fn).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(p.name == "historico" or p.kind is inspect.Parameter.VAR_KEYWORD for p in parametros)


def _executar_caso(case: EvalCase, pipeline_fn: PipelineFn) -> dict[str, Any]:
    """Roda um caso medindo a latência; exceção vira caso errado, não derruba a rodada."""
    com_historico = bool(case.historico) and _aceita_historico(pipeline_fn)
    inicio = time.perf_counter()
    try:
        from src.log_context import log_origin
        with log_origin("avaliacao"):
            if com_historico:
                resposta = pipeline_fn(case.pergunta, historico=case.historico)
            else:
                resposta = pipeline_fn(case.pergunta)
        erro = None
    except Exception as exc:  # noqa: BLE001 - o relatório precisa registrar qualquer falha
        resposta = {}
        erro = f"{type(exc).__name__}: {exc}"
    latencia_ms = int((time.perf_counter() - inicio) * 1000)

    resultado = evaluate_case(case, resposta)
    resultado["latencia_ms"] = latencia_ms
    resultado["erro"] = erro
    if case.depende_de:
        # Registra se o contexto chegou e como a pergunta foi reescrita com ele.
        resultado["depende_de"] = list(case.depende_de)
        resultado["historico_enviado"] = com_historico
        resultado["pergunta_reformulada"] = resposta.get("pergunta_reformulada")
    if erro:
        resultado.update(
            rota_ok=False,
            refusal_ok=False,
            content_ok=False,
            expected_source_ok=False,
            acertou=False,
            citacao_ok=False,
        )
    return resultado


def _resumo_metricas(resultados: list[dict[str, Any]]) -> dict[str, Any]:
    latencias = [r["latencia_ms"] for r in resultados if r.get("latencia_ms") is not None]
    citacoes = [r["citacao_ok"] for r in resultados if r.get("citacao_ok") is not None]
    fontes_esperadas = [r["expected_source_ok"] for r in resultados if r.get("expected_source_ok") is not None]
    escopos: dict[str, int] = defaultdict(int)
    confianca: dict[str, int] = defaultdict(int)
    tokens_total = 0
    tokens_por_etapa: dict[str, int] = defaultdict(int)
    content_cases = [
        r for r in resultados if r.get("content_validation") in {"validated", "failed"}
    ]
    for resultado in resultados:
        escopos[str(resultado.get("escopo_avaliacao", "desconhecido"))] += 1
        if resultado.get("confidence_level"):
            confianca[str(resultado["confidence_level"])] += 1
        metricas = resultado.get("metricas") or {}
        tokens_total += int(metricas.get("tokens_total", 0))
        for etapa, dados in (metricas.get("etapas") or {}).items():
            tokens_por_etapa[etapa] += int(dados.get("tokens", 0))
    total = len(resultados) or 1
    routing_accuracy = sum(1 for r in resultados if r.get("rota_ok")) / total
    refusal_accuracy = sum(1 for r in resultados if r.get("refusal_ok")) / total
    content_accuracy = (
        sum(1 for r in content_cases if r.get("content_ok")) / len(content_cases)
        if content_cases else None
    )
    citation_presence = sum(1 for r in resultados if int(r.get("n_fontes") or 0) > 0) / total if total else 0.0
    expected_source_accuracy = (
        sum(1 for value in fontes_esperadas if value) / len(fontes_esperadas)
        if fontes_esperadas
        else None
    )
    return {
        "erros": sum(1 for r in resultados if r.get("erro")),
        "erros_provedor": sum(1 for r in resultados if r.get("erro_provedor")),
        "citacao_avaliada": len(citacoes),
        "citacao_ok": sum(citacoes),
        "citation_accuracy": round(sum(citacoes) / len(citacoes), 4) if citacoes else None,
        "routing_accuracy": round(routing_accuracy, 4),
        "refusal_accuracy": round(refusal_accuracy, 4),
        "content_accuracy": None if content_accuracy is None else round(content_accuracy, 4),
        "citation_presence": round(citation_presence, 4),
        "expected_source_accuracy": None if expected_source_accuracy is None else round(expected_source_accuracy, 4),
        "expected_source_evaluated": len(fontes_esperadas),
        "escopos_avaliados": dict(sorted(escopos.items())),
        "confianca": dict(sorted(confianca.items())),
        "latencia_ms": {
            "media": int(statistics.mean(latencias)) if latencias else 0,
            "mediana": int(statistics.median(latencias)) if latencias else 0,
            "max": max(latencias) if latencias else 0,
            "total": sum(latencias),
        },
        "tokens_total": tokens_total,
        "tokens_por_etapa": dict(sorted(tokens_por_etapa.items())),
    }


def run_eval(cases: list[EvalCase], pipeline_fn: PipelineFn) -> dict[str, Any]:
    """Roda cada caso do golden set contra o pipeline e agrega os resultados."""
    resultados = []
    for indice, case in enumerate(cases, start=1):
        resultado = _executar_caso(case, pipeline_fn)
        resultados.append(resultado)
        if resultado["erro"]:
            status = "ERRO"
        elif resultado.get("erro_provedor"):
            status = "ERRO_PROVEDOR"
        else:
            status = "OK" if resultado["acertou"] else "FALHOU"
        print(
            f"[{indice}/{len(cases)}] {case.id} {status} ({resultado['latencia_ms']} ms)",
            file=sys.stderr,
            flush=True,
        )

    por_tipo: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "acertos": 0})
    for case, resultado in zip(cases, resultados):
        por_tipo[case.tipo]["total"] += 1
        por_tipo[case.tipo]["acertos"] += int(resultado["acertou"])

    total = len(resultados)
    acertos = sum(resultado["acertou"] for resultado in resultados)
    return {
        "total": total,
        "acertos": acertos,
        "taxa_acerto": round(acertos / total, 4) if total else 0.0,
        "por_tipo": dict(sorted(por_tipo.items())),
        "metricas": _resumo_metricas(resultados),
        "resultados": resultados,
    }


def render_report_markdown(report: dict[str, Any], run_id: str, built_at: str) -> str:
    taxa = report["taxa_acerto"]
    lines = [
        "# Relatório de avaliação — golden set",
        "",
        f"- Run: `{run_id}`",
        f"- Gerado em: `{built_at}`",
        f"- Acertos: **{report['acertos']}/{report['total']}** ({taxa:.0%})",
        "",
        "## Por tipo",
        "",
        "| Tipo | Acertos | Total |",
        "|---|---:|---:|",
    ]
    for tipo, stats in report["por_tipo"].items():
        lines.append(f"| {tipo} | {stats['acertos']} | {stats['total']} |")

    metricas = report.get("metricas") or {}
    latencia = metricas.get("latencia_ms") or {}
    if metricas:
        lines.extend(["", "## Métricas", ""])
        if "routing_accuracy" in metricas:
            lines.append(f"- routing_accuracy: **{metricas['routing_accuracy']:.4f}**")
        if "refusal_accuracy" in metricas:
            lines.append(f"- refusal_accuracy: **{metricas['refusal_accuracy']:.4f}**")
        if metricas.get("content_accuracy") is not None:
            lines.append(f"- content_accuracy: **{metricas['content_accuracy']:.4f}**")
        if metricas.get("citation_presence") is not None:
            lines.append(f"- citation_presence: **{metricas['citation_presence']:.4f}**")
        if metricas.get("citation_accuracy") is not None:
            lines.append(
                f"- citation_accuracy: **{metricas['citation_accuracy']:.4f}** "
                f"({metricas['citacao_ok']}/{metricas['citacao_avaliada']} respostas avaliadas)"
            )
        if metricas.get("expected_source_accuracy") is not None:
            lines.append(
                f"- expected_source_accuracy: **{metricas['expected_source_accuracy']:.4f}** "
                f"({metricas['expected_source_evaluated']} respostas avaliadas)"
            )
        if metricas.get("escopos_avaliados"):
            linhas_escopo = ", ".join(
                f"{escopo}={quantidade}" for escopo, quantidade in metricas["escopos_avaliados"].items()
            )
            lines.append(f"- Escopos: {linhas_escopo}")
        if metricas.get("citacao_avaliada"):
            lines.append(f"- Citação correta: **{metricas['citacao_ok']}/{metricas['citacao_avaliada']}**")
        if metricas.get("confianca"):
            confianca = ", ".join(f"{nivel}={qtd}" for nivel, qtd in metricas["confianca"].items())
            lines.append(f"- Confiança: {confianca}")
        lines.append(
            f"- Latência (ms): média {latencia.get('media', 0)}, mediana {latencia.get('mediana', 0)}, "
            f"máx {latencia.get('max', 0)}, total {latencia.get('total', 0)}"
        )
        if metricas.get("tokens_total"):
            lines.append(f"- Tokens: **{metricas['tokens_total']}** no total")
            for etapa, tokens in metricas.get("tokens_por_etapa", {}).items():
                lines.append(f"  - {etapa}: {tokens}")
        lines.append(f"- Erros de execução: {metricas.get('erros', 0)}")
        lines.append(f"- Falhas do provedor do LLM (não contam como recusa): {metricas.get('erros_provedor', 0)}")

    lines.extend(
        [
            "",
            "## Casos",
            "",
            "| ID | Tipo | Rota esperada | Rota obtida | Recusa esperada | Recusa obtida "
            "| Validação de conteúdo "
            "| Fontes | Latência (ms) | Resultado |",
            "|---|---|---|---|---|---|---|---:|---:|---|",
        ]
    )
    for resultado in report["resultados"]:
        if resultado.get("erro"):
            status = "ERRO"
        else:
            status = "OK" if resultado["acertou"] else "FALHOU"
        n_fontes = resultado.get("n_fontes")
        validation_labels = {
            "validated": "validado",
            "failed": "falhou",
            "not_applicable": "não aplicável",
            "not_validatable": "não validável",
        }
        content_validation = validation_labels.get(
            resultado.get("content_validation"), "—"
        )
        lines.append(
            f"| {resultado['id']} | {resultado['tipo']} "
            f"| {', '.join(resultado['bases_esperadas']) or '—'} "
            f"| {', '.join(resultado['bases_obtidas']) or '—'} "
            f"| {resultado['is_refusal_esperado']} | {resultado['is_refusal_obtido']} "
            f"| {content_validation} "
            f"| {'—' if n_fontes is None else n_fontes} | {resultado.get('latencia_ms', '—')} | {status} |"
        )

    com_texto = [r for r in report["resultados"] if r.get("answer") or r.get("erro")]
    if com_texto:
        lines.extend(["", "## Respostas", ""])
        for resultado in com_texto:
            lines.extend([f"### {resultado['id']}", ""])
            if resultado.get("depende_de"):
                enviado = ("enviado" if resultado.get("historico_enviado")
                           else "não enviado: o adaptador não recebe histórico")
                lines.extend([f"Histórico: {', '.join(resultado['depende_de'])} ({enviado})", ""])
                if resultado.get("pergunta_reformulada"):
                    lines.extend([f"Pergunta reescrita: {resultado['pergunta_reformulada']}", ""])
            if resultado.get("erro"):
                lines.append(f"Erro: `{resultado['erro']}`")
            else:
                confianca = resultado.get("confidence_level") or "—"
                lines.extend([f"Confiança: {confianca} · fontes: {resultado.get('n_fontes')}", ""])
                texto = " ".join(str(resultado["answer"]).split())
                lines.append(f"> {texto[:1200]}{'…' if len(texto) > 1200 else ''}")
            lines.append("")
    return "\n".join(lines) + "\n"


def save_run(report: dict[str, Any], output_dir: Path, run_id: str, built_at: str) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "report.json"
    md_path = output_dir / "REPORT.md"
    payload = {"run_id": run_id, "built_at": built_at, **report}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_report_markdown(report, run_id, built_at), encoding="utf-8")
    return json_path, md_path


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def _preparar(pipeline_fn: PipelineFn) -> float | None:
    """Roda ``pipeline_fn.preparar`` (pré-carga, como a API ao subir), se houver.

    Fica fora da latência de cada caso; devolve quanto levou, ou ``None``.
    """
    preparar = getattr(pipeline_fn, "preparar", None)
    if not callable(preparar):
        return None
    inicio = time.perf_counter()
    preparar()
    return time.perf_counter() - inicio


def _load_pipeline(spec: str) -> PipelineFn:
    module_name, _, func_name = spec.partition(":")
    if not module_name or not func_name:
        raise ValueError("use o formato modulo.submodulo:funcao")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, func_name)
    except AttributeError as exc:
        raise ValueError(f"{module_name} não define '{func_name}'") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="valida o golden set sem chamar o pipeline")
    check_parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)

    run_parser = subparsers.add_parser("run", help="roda o golden set contra o pipeline")
    run_parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    run_parser.add_argument(
        "--pipeline",
        required=True,
        help="função a chamar, formato modulo.submodulo:funcao (ex.: src.pipeline:responder)",
    )
    run_parser.add_argument("--output", type=Path, default=DEFAULT_RUNS_DIR)
    run_parser.add_argument("--run-id", default=None)
    run_parser.add_argument("--ids", nargs="+", default=None, help="roda só os casos com esses IDs")

    args = parser.parse_args()

    if not args.golden_set.exists():
        parser.error(f"golden set não encontrado: {args.golden_set}")

    cases = load_golden_set(args.golden_set)

    if args.command == "check":
        por_tipo: dict[str, int] = defaultdict(int)
        for case in cases:
            por_tipo[case.tipo] += 1
        print(json.dumps({"total_casos": len(cases), "por_tipo": dict(sorted(por_tipo.items()))}, ensure_ascii=False))
        return 0

    try:
        pipeline_fn = _load_pipeline(args.pipeline)
    except (ImportError, ValueError) as exc:
        parser.error(
            f"não foi possível carregar --pipeline={args.pipeline}: {exc}. "
            "use eval.adapters:rota_apenas (só roteamento) ou src.pipeline:responder "
            "(pipeline completo)."
        )

    if args.ids:
        desconhecidos = sorted(set(args.ids) - {case.id for case in cases})
        if desconhecidos:
            parser.error(f"IDs fora do golden set: {', '.join(desconhecidos)}")
        cases = [case for case in cases if case.id in set(args.ids)]

    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    preparo = _preparar(pipeline_fn)
    if preparo is not None:
        print(f"pré-carga do pipeline: {preparo:.1f} s (fora da latência dos casos)", file=sys.stderr, flush=True)
    report = run_eval(cases, pipeline_fn)
    json_path, md_path = save_run(report, args.output / run_id, run_id, built_at)
    summary = {
        "run_id": run_id,
        "acertos": report["acertos"],
        "total": report["total"],
        "relatorio_json": _relative(json_path),
        "relatorio_md": _relative(md_path),
    }
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["acertos"] == report["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
