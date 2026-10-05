from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any, Optional, Protocol

import config
from src.obs import Stage, TokenUsage, registrar, token_usage_from_response
from src.schema import (
    Chunk,
    ChunkMetadata,
    ConfidenceLevel,
    Natureza,
    RAGResponse,
    RefusalReason,
    SourceRef,
)
from src.sensitivity import filtrar_chunks_permitidos, mascarar, mascarar_chunks

# --------------------------------------------------------------------------
# Cliente LLM — único ponto de integração real com llm_ecoa.py
# --------------------------------------------------------------------------

GENERATION_MODEL = config.GENERATION_MODEL


class ClienteLLM(Protocol):
    """Recebe as mensagens e devolve o texto gerado — ou a resposta completa
    do SDK OpenAI (com ``choices`` e ``usage``), para que os tokens sejam logados."""

    def __call__(self, mensagens: list[dict]) -> Any: ...


def _cliente_padrao() -> ClienteLLM:
    """Wrapper fino sobre o cliente OpenAI compartilhado de ``llm_ecoa.py``.

    Devolve a resposta completa do SDK para que ``usage`` chegue ao log.
    """
    import llm_ecoa

    diagnosticos: dict = {}

    def _chamar(mensagens: list[dict]) -> Any:
        return llm_ecoa.completar_chat(
            mensagens, diagnosticos=diagnosticos,
            response_format={"type": "json_object"},
        )

    _chamar.diagnosticos = diagnosticos
    return _chamar


def _texto_e_tokens(saida: Any) -> tuple[str, TokenUsage]:
    """Aceita ``str`` (clientes simples/falsos) ou a resposta do SDK OpenAI."""
    if isinstance(saida, str):
        return saida, TokenUsage()
    return saida.choices[0].message.content or "", token_usage_from_response(saida)


# --------------------------------------------------------------------------
# Guardrails determinísticos, antes de chamar o LLM
# --------------------------------------------------------------------------

_PADROES_GUARDRAIL: dict[RefusalReason, list[str]] = {
    RefusalReason.DADOS_BANCARIOS: [
        r"n[uú]mero da conta banc[aá]ria",
        r"ag[eê]ncia e conta",
        r"chave pix",
        r"dados banc[aá]rios (do|da) (fornecedor|empresa|contratad[oa])",
    ],
    RefusalReason.CREDENCIAIS_VAZADAS: [
        r"senha (do|de|da)",
        r"credencia(l|is) de acesso",
        r"token de acesso",
        r"chave de api",
    ],
}


def _checar_guardrail_previo(query: str) -> Optional[RefusalReason]:
    q = query.lower()
    for motivo, padroes in _PADROES_GUARDRAIL.items():
        if any(re.search(p, q) for p in padroes):
            return motivo
    return None


# --------------------------------------------------------------------------
# Prompt
# --------------------------------------------------------------------------

_SYSTEM_PROMPT = """Você é o assistente de um sistema RAG sobre contratações públicas do \
Estado do Rio de Janeiro (dados do PNCP). Responda SOMENTE com base nos trechos de contexto \
fornecidos — nunca invente números, valores ou nomes de órgãos que não estejam no contexto.

Recuse a pergunta (is_refusal=true) quando ela pedir: dados bancários de fornecedor \
(dados_bancarios_fornecedor), credenciais de acesso vazadas (credenciais_vazadas), um juízo \
sobre a legalidade de um contrato específico (juizo_legalidade_contrato), algo fora do escopo \
de contratações públicas do RJ (fora_de_escopo), ou quando NENHUM trecho do contexto tiver \
relação com a pergunta (sem_evidencia).

Com evidência parcial, responda o que os trechos mostram, diga o que falta e use confiança \
baixa ou media. Trecho "sql:" é evidência: use seus números; se diz que nenhum registro \
atende, responda isso citando-o.

Responda SEMPRE e SOMENTE com um objeto JSON, sem nenhum texto antes ou depois, no formato:
{"answer": "...", "is_refusal": false, "refusal_reason": null, \
"confidence_level": "alta|media|baixa|recusado", "sources_used": ["chunk_id", ...]}

sources_used deve conter apenas os chunk_id que você realmente usou, exatamente como \
aparecem no contexto. Se is_refusal for true, sources_used deve ser uma lista vazia."""


def _natureza_label(metadata: ChunkMetadata) -> str:
    """``natureza`` é a classificação Adaptive RAG; ``doc_type`` é o formato do
    arquivo (json/pdf/html) — só cai para ``doc_type`` se natureza não veio."""
    return metadata.natureza.value if metadata.natureza is not None else metadata.doc_type


def _cabecalho(metadata: ChunkMetadata) -> str:
    campos = [
        f"chunk_id={metadata.chunk_id}",
        f"base={metadata.base_id}",
        f"natureza={_natureza_label(metadata)}",
    ]
    # Distingue a ata em si de publicações anexadas (ex.: Diário Oficial).
    if metadata.extras.get("titulo_documento"):
        campos.append(f"documento={metadata.extras['titulo_documento']}")
    if metadata.data_vigencia is not None:
        campos.append(f"vigencia_ate={metadata.data_vigencia.isoformat()}")
    return f"[{' | '.join(campos)}]"


_VAZIOS = (None, "", [], {})


def _sem_vazios(valor: Any) -> Any:
    if isinstance(valor, dict):
        limpo = {chave: _sem_vazios(item) for chave, item in valor.items()}
        return {chave: item for chave, item in limpo.items() if item not in _VAZIOS}
    if isinstance(valor, list):
        return [item for item in (_sem_vazios(i) for i in valor) if item not in _VAZIOS]
    return valor


def _compactar(conteudo: str) -> str:
    """Registro JSON (PNCP) vira JSON compacto, sem campos vazios; texto só
    perde espaços repetidos. O JSON indentado gastava boa parte da janela."""
    try:
        dados = json.loads(conteudo)
    except (TypeError, ValueError):
        return " ".join(conteudo.split())
    dados = _sem_vazios(dados)
    if isinstance(dados, dict):
        # Os campos de evidência devem caber antes de campos administrativos
        # extensos. Apenas reordena o registro, preservando valores e unidades.
        prioridade = (
            "numeroControlePNCP", "valorGlobal", "valorInicial", "valorTotalHomologado",
            "valorTotalEstimado", "valorUnitarioHomologado", "valorTotal", "unidadeMedida",
            "quantidadeHomologada", "nomeRazaoSocialFornecedor", "dataAssinatura", "anoPca",
            "descricao", "descricaoResumida", "objetoContrato", "objetoCompra", "objeto",
            "orgaoEntidade", "unidadeOrgao",
        )
        dados = {**{campo: dados[campo] for campo in prioridade if campo in dados},
                 **{campo: valor for campo, valor in dados.items() if campo not in prioridade}}
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":"))


def _chunks_no_orcamento(chunks: list[Chunk]) -> list[Chunk]:
    """Chunks, na ordem da recuperação, que cabem na janela do modelo.

    Cada conteúdo é compactado e cortado em ``GENERATION_CHUNK_CHARS``; os
    chunks entram até somar ``GENERATION_CONTEXT_CHARS`` (o primeiro sempre
    entra). Sem isso, o Ollama descarta o começo do prompt.
    """
    selecionados: list[Chunk] = []
    usados = 0
    for chunk in chunks:
        conteudo = _compactar(chunk.content)
        if len(conteudo) > config.GENERATION_CHUNK_CHARS:
            conteudo = conteudo[: config.GENERATION_CHUNK_CHARS].rstrip() + " [...]"
        tamanho = len(_cabecalho(chunk.metadata)) + len(conteudo)
        if selecionados and usados + tamanho > config.GENERATION_CONTEXT_CHARS:
            break
        selecionados.append(chunk.model_copy(update={"content": conteudo}))
        usados += tamanho
    return selecionados


def _montar_prompt(query: str, chunks: list[Chunk]) -> list[dict]:
    contexto = "\n\n".join(f"{_cabecalho(c.metadata)}\n{c.content}" for c in chunks)
    # A instrução de formato se repete no fim: se o prompt passar da janela,
    # o Ollama corta o começo, e o fim é o que o modelo ainda vê.
    mensagem_usuario = (
        f"Contexto:\n{contexto}\n\nPergunta: {query}\n\n"
        "Responda somente com o objeto JSON no formato pedido, citando em "
        "sources_used os chunk_id usados."
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": mensagem_usuario},
    ]


# --------------------------------------------------------------------------
# Parsing defensivo da saída do LLM
# --------------------------------------------------------------------------

def _extrair_json(bruto: str) -> dict:
    texto = bruto.strip()
    texto = re.sub(r"^```(?:json)?", "", texto).strip()
    texto = re.sub(r"```$", "", texto).strip()
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fim == -1 or fim < inicio:
        raise ValueError("Nenhum objeto JSON encontrado na resposta do LLM")
    return json.loads(texto[inicio : fim + 1])


def _recusa(
    query: str,
    bases_consultadas: list[Natureza],
    motivo: RefusalReason,
) -> RAGResponse:
    respostas_padrao = {
        RefusalReason.SEM_EVIDENCIA: (
            "Não encontrei evidência suficiente nas bases consultadas para responder com segurança."
        ),
        RefusalReason.DADOS_BANCARIOS: "Não posso fornecer dados bancários de fornecedores.",
        RefusalReason.CREDENCIAIS_VAZADAS: "Não posso fornecer credenciais de acesso.",
        RefusalReason.JUIZO_LEGALIDADE: (
            "Não posso emitir juízo sobre a legalidade de um contrato específico."
        ),
        RefusalReason.FORA_DE_ESCOPO: (
            "Essa pergunta está fora do escopo de contratações públicas do RJ."
        ),
    }
    return RAGResponse(
        query=query,
        answer=respostas_padrao.get(motivo, respostas_padrao[RefusalReason.SEM_EVIDENCIA]),
        bases_consultadas=bases_consultadas,
        sources_used=[],
        confidence_level=ConfidenceLevel.RECUSADO,
        is_refusal=True,
        refusal_reason=motivo,
    )


# Nomes que o LLM usa no lugar das chaves do contrato (o qwen3-8b devolve
# "sources" e "confidence": 0.95 apesar do prompt). O primeiro é o canônico.
_ALIASES_CHAVES: dict[str, tuple[str, ...]] = {
    "sources_used": ("sources_used", "sources", "fontes"),
    "confidence_level": ("confidence_level", "confidence", "confianca", "confiança"),
}

# Cortes para converter uma confiança numérica (0 a 1) em ConfidenceLevel.
_CONFIANCA_ALTA = 0.8
_CONFIANCA_MEDIA = 0.5


def _campo(dados: dict, chave: str, alternativas_usadas: list[str]) -> Any:
    """Lê ``chave`` ou um de seus aliases, anotando quando veio por alias."""
    for nome in _ALIASES_CHAVES[chave]:
        if nome in dados:
            if nome != chave:
                alternativas_usadas.append(nome)
            return dados[nome]
    return None


def _nivel_de_confianca(valor: Any) -> ConfidenceLevel:
    if isinstance(valor, bool):
        return ConfidenceLevel.BAIXA
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        try:
            return ConfidenceLevel(str(valor).lower())
        except ValueError:
            return ConfidenceLevel.BAIXA
    if numero >= _CONFIANCA_ALTA:
        return ConfidenceLevel.ALTA
    if numero >= _CONFIANCA_MEDIA:
        return ConfidenceLevel.MEDIA
    return ConfidenceLevel.BAIXA


def _parsear_resposta_llm(
    bruto: str,
    query: str,
    chunks: list[Chunk],
    bases_consultadas: list[Natureza],
) -> tuple[RAGResponse, Optional[str], list[str], list[str]]:
    """Devolve a resposta, o motivo se a saída do LLM for inválida, as chaves
    fora do contrato que foram aceitas como alias e os chunk_ids citados pelo
    LLM que não estavam entre os chunks recuperados (descartados)."""
    chunks_por_id = {c.metadata.chunk_id: c for c in chunks}
    alternativas_usadas: list[str] = []
    citacoes_descartadas: list[str] = []
    try:
        dados = _extrair_json(bruto)
        answer = str(dados["answer"]).strip()
        is_refusal = bool(dados.get("is_refusal", False))

        confidence_level = _nivel_de_confianca(_campo(dados, "confidence_level", alternativas_usadas))

        sources_used: list[SourceRef] = []
        for chunk_id in _campo(dados, "sources_used", alternativas_usadas) or []:
            chunk = chunks_por_id.get(chunk_id)
            if chunk is None:
                citacoes_descartadas.append(str(chunk_id))
                continue
            sources_used.append(
                SourceRef(
                    chunk_id=chunk.metadata.chunk_id,
                    base_id=chunk.metadata.base_id,
                    source_file=chunk.metadata.source_file,
                )
            )

        refusal_reason: Optional[RefusalReason] = None
        if is_refusal:
            try:
                refusal_reason = RefusalReason(dados.get("refusal_reason"))
            except ValueError:
                refusal_reason = RefusalReason.SEM_EVIDENCIA
            sources_used = []
            confidence_level = ConfidenceLevel.RECUSADO
        elif confidence_level == ConfidenceLevel.RECUSADO:
            confidence_level = ConfidenceLevel.BAIXA

        resposta = RAGResponse(
            query=query,
            answer=answer,
            bases_consultadas=bases_consultadas,
            sources_used=sources_used,
            confidence_level=confidence_level,
            is_refusal=is_refusal,
            refusal_reason=refusal_reason,
        )
        return resposta, None, alternativas_usadas, citacoes_descartadas
    except Exception as exc:
        # Saída inválida do LLM vira recusa segura; o motivo vai para o log.
        return (
            _recusa(query, bases_consultadas, RefusalReason.SEM_EVIDENCIA),
            f"{type(exc).__name__}: {exc}",
            alternativas_usadas,
            citacoes_descartadas,
        )


def _logar_evento(
    run_id: str,
    query_id: str,
    status: str,
    inicio: float,
    resposta: RAGResponse,
    *,
    tokens: Optional[TokenUsage] = None,
    modelo: Optional[str] = None,
    **extra,
) -> None:
    """Registra o stage ``geracao`` no contrato de ``src/obs.py``."""
    if resposta.is_refusal:
        output = f"recusa: {resposta.refusal_reason.value if resposta.refusal_reason else '-'}"
    else:
        output = f"resposta {resposta.confidence_level.value}; {len(resposta.sources_used)} fontes"
    registrar(
        run_id=run_id,
        query_id=query_id,
        stage=Stage.GERACAO,
        status=status,
        latencia_ms=(time.monotonic() - inicio) * 1000,
        input=resposta.query,
        output=output,
        tokens=tokens,
        modelo=modelo,
        extra={
            "confidence_level": resposta.confidence_level.value,
            "is_refusal": resposta.is_refusal,
            "bases_consultadas": [b.value for b in resposta.bases_consultadas],
            "num_sources": len(resposta.sources_used),
            **extra,
        },
    )


def _logar_validacao(
    run_id: str,
    query_id: str,
    inicio: float,
    resposta: RAGResponse,
    *,
    erro_parsing: Optional[str],
    citacoes_descartadas: list[str],
    chaves_alias: list[str],
    mascaramentos_resposta: dict[str, int],
    recusa_convertida: Optional[str] = None,
) -> None:
    """Registra o stage ``validacao``: o que foi checado/corrigido na saída do LLM.

    Separado de ``geracao`` (chamada ao LLM, tokens) para o log mostrar quanto
    da resposta final veio de correção: JSON inválido virou recusa, citação de
    chunk não recuperado foi descartada, dado pessoal foi mascarado.
    """
    registrar(
        run_id=run_id,
        query_id=query_id,
        stage=Stage.VALIDACAO,
        status="saida_invalida" if erro_parsing else "ok",
        latencia_ms=(time.monotonic() - inicio) * 1000,
        input=resposta.query,
        output=f"{len(resposta.sources_used)} fontes válidas; {len(citacoes_descartadas)} descartadas",
        extra={
            "json_valido": erro_parsing is None,
            "citacoes_descartadas": citacoes_descartadas,
            "chaves_fora_do_contrato": chaves_alias,
            "mascaramentos_resposta": mascaramentos_resposta,
            "recusa_convertida": recusa_convertida,
            "confidence_level": resposta.confidence_level.value,
            "is_refusal": resposta.is_refusal,
        },
    )


# --------------------------------------------------------------------------
# API pública
# --------------------------------------------------------------------------

def gerar_resposta(
    query: str,
    chunks: list[Chunk],
    bases_consultadas: list[Natureza],
    *,
    cliente: Optional[ClienteLLM] = None,
    run_id: str = "sem-run-id",
    query_id: Optional[str] = None,
    refusal_reason_hint: Optional[RefusalReason] = None,
) -> RAGResponse:
    """
    Estágio "geracao": recebe a pergunta e os chunks já recuperados/rerankeados,
    devolve sempre uma RAGResponse válida (nunca uma exception do LLM/parsing).

    ``refusal_reason_hint`` é usado só quando ``chunks`` vem vazio (nenhuma base
    foi consultada) — permite a quem chama (ex.: src/pipeline.py, a partir da
    justificativa de src/router.py) dizer *por que* não há chunks (fora de
    escopo, juízo de legalidade...) em vez de sempre cair em SEM_EVIDENCIA.
    """
    query_id = query_id or str(uuid.uuid4())
    inicio = time.monotonic()

    motivo_previo = _checar_guardrail_previo(query)
    if motivo_previo is not None:
        resposta = _recusa(query, bases_consultadas, motivo_previo)
        _logar_evento(run_id, query_id, "bloqueado_guardrail_previo", inicio, resposta)
        return resposta

    # Política de sensibilidade (src/sensitivity.py): chunk restrito nunca chega
    # ao LLM, e CPF/telefone/e-mail pessoal são mascarados antes do prompt.
    chunks, restritos_descartados = filtrar_chunks_permitidos(chunks)
    chunks, mascaramentos = mascarar_chunks(chunks)
    sensibilidade = {"restritos_descartados": restritos_descartados, "mascaramentos": mascaramentos}

    if not chunks:
        resposta = _recusa(query, bases_consultadas, refusal_reason_hint or RefusalReason.SEM_EVIDENCIA)
        _logar_evento(run_id, query_id, "sem_chunks", inicio, resposta, **sensibilidade)
        return resposta

    chunks_prompt = _chunks_no_orcamento(chunks)
    sensibilidade["chunks_no_prompt"] = len(chunks_prompt)
    sensibilidade["chunks_fora_do_orcamento"] = len(chunks) - len(chunks_prompt)
    chunks = chunks_prompt
    mensagens = _montar_prompt(query, chunks)
    cliente = cliente or _cliente_padrao()

    try:
        bruto, tokens = _texto_e_tokens(cliente(mensagens))
    except Exception as exc:
        resposta = _recusa(query, bases_consultadas, RefusalReason.SEM_EVIDENCIA)
        _logar_evento(
            run_id, query_id, "erro_llm", inicio, resposta,
            modelo=GENERATION_MODEL, erro=f"{type(exc).__name__}: {exc}", **sensibilidade,
        )
        return resposta

    inicio_validacao = time.monotonic()
    resposta, erro_parsing, chaves_alias, citacoes_descartadas = _parsear_resposta_llm(
        bruto, query, chunks, bases_consultadas
    )
    if erro_parsing:
        sensibilidade["erro_parsing"] = erro_parsing
    if chaves_alias:
        sensibilidade["chaves_fora_do_contrato"] = chaves_alias
    resposta, recusa_convertida = _converter_recusa_indevida(resposta, bases_consultadas, erro_parsing)
    if recusa_convertida:
        sensibilidade["recusa_convertida"] = recusa_convertida
    # Defesa em profundidade: o dado pode ter vindo da própria pergunta.
    answer_mascarada, mascaramentos_resposta = mascarar(resposta.answer)
    if mascaramentos_resposta:
        resposta = resposta.model_copy(update={"answer": answer_mascarada})
        sensibilidade["mascaramentos_resposta"] = mascaramentos_resposta
    _logar_evento(
        run_id, query_id, "saida_invalida" if erro_parsing else "ok", inicio, resposta,
        tokens=tokens, modelo=GENERATION_MODEL, **sensibilidade,
    )
    _logar_validacao(
        run_id, query_id, inicio_validacao, resposta,
        erro_parsing=erro_parsing,
        citacoes_descartadas=citacoes_descartadas,
        chaves_alias=chaves_alias,
        mascaramentos_resposta=mascaramentos_resposta,
        recusa_convertida=recusa_convertida,
    )
    return resposta


# Recusas que o LLM pode marcar por engano; as de guardrail (dados bancários,
# credenciais, juízo de legalidade) são decididas antes do LLM e nunca mudam.
_RECUSAS_CONVERTIVEIS = (RefusalReason.FORA_DE_ESCOPO, RefusalReason.SEM_EVIDENCIA)


def _converter_recusa_indevida(
    resposta: RAGResponse, bases_consultadas: list[Natureza], erro_parsing: Optional[str]
) -> tuple[RAGResponse, Optional[str]]:
    """Recusa com texto, numa pergunta que o roteador pôs no escopo, vira resposta.

    O qwen3-8b escreve uma resposta útil ("o contexto só traz dados municipais,
    com mediana de R$ 519.500") e ainda marca ``is_refusal`` como fora de escopo
    ou sem evidência. Se o roteador escolheu bases, a pergunta está no escopo, e
    dizer o que os dados mostram e o que falta é a resposta esperada; ela sai com
    confiança baixa, e o log registra a conversão em ``recusa_convertida``.
    """
    if (
        erro_parsing
        or not resposta.is_refusal
        or not bases_consultadas
        or resposta.refusal_reason not in _RECUSAS_CONVERTIVEIS
        or not resposta.answer.strip()
    ):
        return resposta, None
    motivo = resposta.refusal_reason.value
    return resposta.model_copy(update={
        "is_refusal": False, "refusal_reason": None, "confidence_level": ConfidenceLevel.BAIXA,
    }), motivo