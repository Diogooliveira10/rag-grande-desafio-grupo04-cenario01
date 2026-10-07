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


def _schema_resposta(chunk_ids: Optional[list[str]] = None) -> dict:
    """Contrato de saída do modelo; fontes são IDs das evidências fornecidas."""
    return {"type": "object", "additionalProperties": False,
            "required": ["answer", "is_refusal", "refusal_reason", "confidence_level", "sources_used"],
            "properties": {
                "answer": {"type": "string", "minLength": 1},
                "is_refusal": {"type": "boolean"},
                "refusal_reason": {"type": ["string", "null"], "enum": [
                    None, *(r.value for r in RefusalReason if r is not RefusalReason.ERRO_PROVEDOR)]},
                "confidence_level": {"type": "string", "enum": [r.value for r in ConfidenceLevel]},
                "sources_used": {"type": "array", "items": {"type": "string", **({"enum": chunk_ids} if chunk_ids else {})}},
            }}


def _cliente_padrao(schema: Optional[dict] = None) -> ClienteLLM:
    """Wrapper fino sobre o cliente OpenAI compartilhado de ``llm_ecoa.py``.

    Devolve a resposta completa do SDK para que ``usage`` chegue ao log.
    """
    import llm_ecoa

    diagnosticos: dict = {}

    def _chamar(mensagens: list[dict]) -> Any:
        return llm_ecoa.completar_chat(
            mensagens, diagnosticos=diagnosticos,
            response_format=({"type": "json_schema", "json_schema": {"name": "query_plan", "schema": schema}}
                             if schema is not None else {"type": "json_object"}),
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
"confidence_level": "alta|media|baixa|recusado", "sources_used": ["evidence_id", ...]}

sources_used deve conter apenas os evidence_id que você realmente usou, exatamente como \
aparecem no contexto. Resposta factual baseada no contexto deve citar pelo menos uma \
evidência relevante. Não invente evidence_id; se não houver evidência relevante, recuse. \
Se is_refusal for true, sources_used deve ser uma lista vazia."""

_SYSTEM_PROMPT += """
Responda separadamente a cada intenção da pergunta. Uma parte respondível não desaparece
porque outra parte carece de evidência: explique a lacuna nessa parte, mantendo as fontes
da parte sustentada. Cite o evidence_id entre colchetes, ex.: [E1], junto da afirmação que
ele sustenta; não escreva chunk_id, nome de base nem outros identificadores no texto.
Categoria do processo (ex.: Compras, Informática) NÃO é modalidade de licitação.
Quando modalidade não estiver no registro, diga que não está informada. amparoLegal.nome
é uma referência legal explícita; não declare inexistência de norma se ela está citada.
Não associe regras de dispensa a um pregão ou de outro processo ao registro em análise.
Relacione cada norma à modalidade explicitamente identificada pela fonte.
Cláusula que prevê atraso, penalidade ou cancelamento não comprova ocorrência desses
eventos. Para afirmar ocorrência, cite relato, decisão ou registro do fato.
Valor global/estimado/contratado não é preço unitário nem pagamento realizado.
Zero registrado é zero, não ausência de informação. Quando houver valorGlobal=0
e valorInicial positivo, informe os dois campos com seus nomes e significados distintos.
Tipo, variante e unidade pedidos precisam constar na fonte: uma ambulância genérica
não comprova tipo D, nem manutenção de sistemas comprova manutenção de computadores.
Responda cada parte de uma pergunta composta em frase completa, explicando o que
é comprovado e o que falta. Não termine apenas com uma introdução seguida de dois-pontos.
Se calculo_disponivel=false, nenhum cálculo analítico foi executado para essa intenção:
os registros servem como exemplos, não como estatística. Não faça a conta pelo LLM.
Use cálculos fornecidos pelo SQL, com período, filtros e tamanho da amostra. Não faça
média de médias mensais, nem conclua tendência crescente por um pico ou média anual.
Comparação primeiro/último mês não prova crescimento monotônico. Não extrapole a amostra
para todas as contratações do RJ. Ausência na amostra não prova inexistência no universo.
Evidência com filtros relaxados é contextual: não comprova atendimento ao recorte original.
Vigência histórica não verificada não prova que a norma estava vigente no ano solicitado.
Trechos com sintetico=true são cenários fictícios: identifique-os como sintéticos na resposta.
Não use conteúdo sintético para comprovar fatos reais, pagamentos ou ocorrências do PNCP.
Em conversas, a data do e-mail, mensagem ou cabeçalho "Data da conversa" é a data
DA COMUNICAÇÃO, não a data da entrega, pagamento ou execução mencionada no texto.
Para local/data/horário de recebimento, priorize a mensagem que CONFIRMA o agendamento
ou autoriza a operação, não o pedido inicial de confirmação. Preserve detalhes explícitos
como subsolo, dia e horário. Agendamento autorizado não comprova entrega já realizada.
Snapshot principalmente de 2025 não confirma oportunidades abertas hoje.
Toda resposta, inclusive recusa, precisa de texto não vazio.
"""


def _natureza_label(metadata: ChunkMetadata) -> str:
    """``natureza`` é a classificação Adaptive RAG; ``doc_type`` é o formato do
    arquivo (json/pdf/html) — só cai para ``doc_type`` se natureza não veio."""
    return metadata.natureza.value if metadata.natureza is not None else metadata.doc_type


def _cabecalho(metadata: ChunkMetadata, evidence_id: str | None = None) -> str:
    evidence_id = evidence_id or metadata.chunk_id
    campos = [
        f"evidence_id={evidence_id}",
        f"base={metadata.base_id}",
        f"natureza={_natureza_label(metadata)}",
    ]
    if metadata.extras.get("sintetico"):
        campos.append("sintetico=true; cenário fictício, não comprova fatos reais")
    # Distingue a ata em si de publicações anexadas (ex.: Diário Oficial).
    if metadata.extras.get("titulo_documento"):
        campos.append(f"documento={metadata.extras['titulo_documento']}")
    if metadata.data_vigencia is not None:
        campos.append(f"vigencia_ate={metadata.data_vigencia.isoformat()}")
    # Metadados de src/qualidade_corpus.py: início da vigência das normas,
    # valor simbólico do PNCP e modalidade herdada da compra.
    if metadata.extras.get("data_vigencia_inicio"):
        campos.append(f"vigente_desde={metadata.extras['data_vigencia_inicio']}")
    if metadata.extras.get("valor_simbolico"):
        campos.append("valor_simbolico=true; R$ 0,00 ou R$ 0,01 no PNCP não é o custo real")
    if metadata.extras.get("modalidade_fonte") == "compra_vinculada":
        campos.append(f"modalidade={metadata.extras['modalidade']} (da compra vinculada)")
    if metadata.extras.get("subpergunta"):
        campos.append(f"intencao={metadata.extras['intencao']}: {metadata.extras['subpergunta']}")
    if metadata.extras.get("filtros_relaxados"):
        campos.append("recorte_nao_comprovado=" + json.dumps(metadata.extras["filtros_relaxados"], ensure_ascii=False))
    if metadata.extras.get("calculo_disponivel") is False:
        campos.append("calculo_disponivel=false")
    if metadata.extras.get("vigencia_historica_verificada") is False or (
        metadata.natureza == Natureza.NORMATIVA and metadata.data_vigencia is None):
        campos.append("vigencia_historica=não verificada")
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
            "modalidadeNome", "amparoLegal", "categoriaProcesso", "orgaoEntidade", "unidadeOrgao",
        )
        dados = {**{campo: dados[campo] for campo in prioridade if campo in dados},
                 **{campo: valor for campo, valor in dados.items() if campo not in prioridade}}
    return json.dumps(dados, ensure_ascii=False, separators=(",", ":"))


def _representar_registro(conteudo: str) -> str:
    """Visão compacta dos campos relevantes, sem alterar o documento original."""
    try:
        registro = json.loads(conteudo)
    except (ValueError, TypeError):
        return conteudo
    if not isinstance(registro, dict):
        return conteudo
    campos = ("numeroControlePNCP", "numeroControlePNCPAta", "objetoContrato", "objetoCompra",
        "objetoContratacao", "descricao", "descricaoResumida", "nomeRazaoSocialFornecedor",
        "dataAssinatura", "dataPublicacaoPncp", "dataAberturaProposta", "dataEncerramentoProposta",
        "dataVigenciaInicio", "dataVigenciaFim", "anoPca", "valorGlobal", "valorInicial",
        "valorTotalHomologado", "valorTotalEstimado", "valorUnitarioHomologado", "valorUnitarioEstimado",
        "valorTotal", "valor", "quantidade", "quantidadeHomologada", "unidadeMedida", "amparoLegal",
        "orgaoEntidade", "unidadeOrgao", "categoriaProcesso", "tipoContrato", "modalidadeNome")
    view = {k: registro[k] for k in campos if k in registro and registro[k] is not None}
    if "categoriaProcesso" in registro or "objetoContrato" in registro or "objetoCompra" in registro:
        view["modalidade_licitacao"] = registro.get("modalidadeNome") or "não informada neste registro"
    return json.dumps(view, ensure_ascii=False, separators=(",", ":")) if view else conteudo


def _chunks_no_orcamento(
    chunks: list[Chunk], bases_requeridas: list[Natureza] | None = None,
) -> list[Chunk]:
    """Limita contexto preservando candidatos por intenção, base e natureza."""
    preparados = []
    for chunk in chunks:
        conteudo = _representar_registro(_compactar(chunk.content))
        if len(conteudo) > config.GENERATION_CHUNK_CHARS:
            conteudo = conteudo[:config.GENERATION_CHUNK_CHARS].rstrip() + " [...]"
        preparados.append(chunk.model_copy(update={"content": conteudo}))
    primeiros, restantes, grupos = [], [], set()
    for chunk in preparados:
        grupo = (chunk.metadata.extras.get("intencao", 0), chunk.metadata.base_id)
        if grupo not in grupos:
            primeiros.append(chunk)
            grupos.add(grupo)
        else:
            restantes.append(chunk)
    ordem = primeiros + restantes
    reservas = []
    naturezas = list(dict.fromkeys(bases_requeridas or []))
    if len(naturezas) > 1:
        reservas = [next(i for i, chunk in enumerate(ordem) if chunk.metadata.natureza == natureza)
                    for natureza in naturezas if any(chunk.metadata.natureza == natureza for chunk in ordem)]
    indices = reservas + [i for i in range(len(ordem)) if i not in reservas]
    escolhidos, usados = [], 0
    for i in indices:
        chunk = ordem[i]
        tamanho = len(_cabecalho(chunk.metadata, f"E{len(escolhidos)+1}")) + len(chunk.content)
        if tamanho > config.GENERATION_CONTEXT_CHARS - usados:
            continue
        escolhidos.append(i)
        usados += tamanho
    escolhidos.sort()
    return [ordem[i] for i in escolhidos]


def _montar_prompt(query: str, chunks: list[Chunk]) -> list[dict]:
    contexto = "\n\n".join(
        f"{_cabecalho(chunk.metadata, f'E{index}')}\n{chunk.content}"
        for index, chunk in enumerate(chunks, start=1)
    )
    # A instrução de formato se repete no fim: se o prompt passar da janela,
    # o Ollama corta o começo, e o fim é o que o modelo ainda vê.
    mensagem_usuario = (
        f"Contexto:\n{contexto}\n\nPergunta: {query}\n\n"
        "Responda somente com o objeto JSON no formato pedido, citando em "
        "sources_used os evidence_id usados."
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
        RefusalReason.ERRO_PROVEDOR: (
            "Não consegui gerar a resposta agora por uma falha no serviço de geração. "
            "Tente novamente em instantes."
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
    chunks_por_evidence_id = {
        f"E{index}": chunk for index, chunk in enumerate(chunks, start=1)
    }
    alternativas_usadas: list[str] = []
    citacoes_descartadas: list[str] = []
    try:
        dados = _extrair_json(bruto)
        answer = str(dados["answer"]).strip()
        is_refusal = bool(dados.get("is_refusal", False))

        confidence_level = _nivel_de_confianca(_campo(dados, "confidence_level", alternativas_usadas))

        sources_used: list[SourceRef] = []
        fontes_adicionadas: set[str] = set()
        for referencia in _campo(dados, "sources_used", alternativas_usadas) or []:
            referencia = referencia if isinstance(referencia, str) else str(referencia)
            chunk = chunks_por_evidence_id.get(referencia) or chunks_por_id.get(referencia)
            if chunk is None:
                citacoes_descartadas.append(referencia)
                continue
            if chunk.metadata.chunk_id in fontes_adicionadas:
                continue
            fontes_adicionadas.add(chunk.metadata.chunk_id)
            sources_used.append(
                SourceRef(
                    chunk_id=chunk.metadata.chunk_id,
                    base_id=chunk.metadata.base_id,
                    source_file=chunk.metadata.source_file,
                    origem=chunk.metadata.origem if (chunk.metadata.origem or "").startswith(("https://", "http://")) else None,
                )
            )

        refusal_reason: Optional[RefusalReason] = None
        if is_refusal:
            try:
                refusal_reason = RefusalReason(dados.get("refusal_reason"))
            except ValueError:
                refusal_reason = RefusalReason.SEM_EVIDENCIA
            if refusal_reason is RefusalReason.ERRO_PROVEDOR:
                # Só o código marca falha de infraestrutura; o LLM não decide isso.
                refusal_reason = RefusalReason.SEM_EVIDENCIA
            sources_used = []
            confidence_level = ConfidenceLevel.RECUSADO
            if not answer:
                answer = _recusa(query, bases_consultadas, refusal_reason).answer
        elif confidence_level == ConfidenceLevel.RECUSADO:
            confidence_level = ConfidenceLevel.BAIXA
        if not answer:
            raise ValueError("Resposta sem texto")

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
        # É falha do serviço, não falta de evidência: o usuário vê "tente de novo".
        return (
            _recusa(query, bases_consultadas, RefusalReason.ERRO_PROVEDOR),
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
            # A resposta como saiu da etapa, para acompanhar o dado até o usuário.
            "resposta": {
                "answer": _texto_para_log(resposta.answer),
                "sources_used": [fonte.chunk_id for fonte in resposta.sources_used],
                "refusal_reason": resposta.refusal_reason.value if resposta.refusal_reason else None,
            },
            **extra,
        },
    )


# Tamanho máximo de cada texto do prompt e da resposta gravado no log. O
# prompt fica em torno de GENERATION_CONTEXT_CHARS; o limite só corta exceções.
_LIMITE_DE_TEXTO_NO_LOG = 8000


def _texto_para_log(texto: str) -> str:
    """Texto mascarado (CPF, telefone, e-mail pessoal) e cortado no limite do log."""
    mascarado = mascarar(texto or "")[0]
    if len(mascarado) <= _LIMITE_DE_TEXTO_NO_LOG:
        return mascarado
    return mascarado[:_LIMITE_DE_TEXTO_NO_LOG] + "…"


def _prompt_para_log(mensagens: list[dict]) -> list[dict[str, str]]:
    """Mensagens enviadas ao LLM como vão para o log. Os chunks restritos já
    ficaram de fora antes do prompt (``filtrar_chunks_permitidos``)."""
    return [
        {"role": str(mensagem.get("role", "")), "content": _texto_para_log(str(mensagem.get("content", "")))}
        for mensagem in mensagens
    ]


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
            "citacoes_aceitas": [source.chunk_id for source in resposta.sources_used],
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

    chunks_prompt = _chunks_no_orcamento(chunks, bases_requeridas=bases_consultadas)
    sensibilidade["chunks_no_prompt"] = len(chunks_prompt)
    sensibilidade["chunks_fora_do_orcamento"] = len(chunks) - len(chunks_prompt)
    sensibilidade["evidencias_no_prompt"] = [chunk.metadata.chunk_id for chunk in chunks_prompt]
    sensibilidade["prompt_chunk_ids"] = [chunk.metadata.chunk_id for chunk in chunks_prompt]
    sensibilidade["prompt_evidence_ids"] = {
        f"E{index}": chunk.metadata.chunk_id for index, chunk in enumerate(chunks_prompt, start=1)
    }
    selecionados_ids = {chunk.metadata.chunk_id for chunk in chunks_prompt}
    sensibilidade["evidencias_fora_do_prompt"] = [
        chunk.metadata.chunk_id for chunk in chunks if chunk.metadata.chunk_id not in selecionados_ids
    ]
    sensibilidade["chunks_excluidos_orcamento"] = sensibilidade["evidencias_fora_do_prompt"]
    chunks = chunks_prompt
    mensagens = _montar_prompt(query, chunks)
    sensibilidade["prompt"] = _prompt_para_log(mensagens)
    cliente = cliente or _cliente_padrao(_schema_resposta([f"E{i}" for i in range(1, len(chunks) + 1)]))

    try:
        bruto, tokens = _texto_e_tokens(cliente(mensagens))
    except Exception as exc:
        llm_info = dict(getattr(cliente, "diagnosticos", {}))
        sensibilidade["llm"] = llm_info
        resposta = _recusa(query, bases_consultadas, RefusalReason.ERRO_PROVEDOR)
        _logar_evento(
            run_id, query_id, "erro_llm", inicio, resposta,
            modelo=llm_info.get("model", GENERATION_MODEL), erro=f"{type(exc).__name__}: {exc}", **sensibilidade,
        )
        return resposta

    llm_info = dict(getattr(cliente, "diagnosticos", {}))
    sensibilidade["llm"] = llm_info
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
    resposta, ids_no_texto = _numerar_citacoes(resposta, chunks)
    if ids_no_texto:
        sensibilidade["ids_internos_no_texto"] = ids_no_texto
    # Defesa em profundidade: o dado pode ter vindo da própria pergunta.
    answer_mascarada, mascaramentos_resposta = mascarar(resposta.answer)
    if mascaramentos_resposta:
        resposta = resposta.model_copy(update={"answer": answer_mascarada})
        sensibilidade["mascaramentos_resposta"] = mascaramentos_resposta
    _logar_evento(
        run_id, query_id, "saida_invalida" if erro_parsing else "ok", inicio, resposta,
        tokens=tokens, modelo=llm_info.get("model", GENERATION_MODEL), **sensibilidade,
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


# Identificadores internos que o LLM escreve no texto: evidence_id do prompt
# (E1), chunk_id completo (base:hash:chunk-...), resultado SQL (sql:tabela:...)
# e pedaços de chunk_id (chunk-1-7303119b4364, 10-f8902f79c194).
_ID_INTERNO = (
    r"(?:E\d{1,3}\b|sql:[a-z_]+(?::[\w.-]+)+|[a-z_]+:[0-9a-f]{8,}(?::[\w-]+)+"
    r"|chunk-\d+-[0-9a-f]{6,}|\b\d+-[0-9a-f]{12}\b)"
)
_ROTULO_ID = r"(?:evidence_id|chunk_id|evid[eê]ncias?|trechos?|chunks?|fontes?)"
_SEP_ID = r"\s*(?:,|;|\be\b)\s*"
# Grupo entre parênteses ou colchetes: "(evidence_id=E1)", "[E1, E2]", "(chunks 10-… e 23-…)".
_RE_GRUPO_IDS = re.compile(
    rf"\s*[\(\[]\s*(?:{_ROTULO_ID}\s*[=:]?\s*)?({_ID_INTERNO}(?:{_SEP_ID}(?:{_ROTULO_ID}\s*[=:]?\s*)?{_ID_INTERNO})*)\s*[\)\]]",
    re.IGNORECASE,
)
# Fora de grupo, só o que não tem como ser texto comum: rótulo explícito ou id completo.
_RE_ID_SOLTO = re.compile(
    rf"(?:{_ROTULO_ID}\s*[=:]\s*{_ID_INTERNO}|sql:[a-z_]+(?::[\w.-]+)+|[a-z_]+:[0-9a-f]{{8,}}(?::[\w-]+)+)",
    re.IGNORECASE,
)


def _numerar_citacoes(resposta: RAGResponse, chunks: list[Chunk]) -> tuple[RAGResponse, int]:
    """Troca identificadores internos do texto por ``[n]``, o número da fonte em
    ``sources_used``; id que não é fonte aceita sai do texto.

    O prompt pede o evidence_id junto da afirmação (liga cada parte da resposta
    à sua fonte), mas ``E1`` ou ``contratos_estruturado:…`` não dizem nada ao
    usuário. Devolve a resposta e quantos identificadores foram trocados.
    """
    numero_da_fonte = {fonte.chunk_id: indice for indice, fonte in enumerate(resposta.sources_used, start=1)}
    por_evidence_id = {f"E{indice}": chunk.metadata.chunk_id for indice, chunk in enumerate(chunks, start=1)}

    def numero(token: str) -> Optional[int]:
        token = re.sub(rf"^{_ROTULO_ID}\s*[=:]?\s*", "", token.strip(), flags=re.IGNORECASE)
        chunk_id = por_evidence_id.get(token, token)
        if chunk_id in numero_da_fonte:
            return numero_da_fonte[chunk_id]
        for completo, indice in numero_da_fonte.items():
            if completo.endswith(token) or completo.endswith(f"chunk-{token}"):
                return indice
        return None

    trocas = 0

    def grupo(match: re.Match) -> str:
        nonlocal trocas
        tokens = re.findall(_ID_INTERNO, match.group(1))
        trocas += len(tokens)
        numeros = sorted({n for n in (numero(t) for t in tokens) if n is not None})
        return f" [{', '.join(map(str, numeros))}]" if numeros else ""

    def solto(match: re.Match) -> str:
        nonlocal trocas
        trocas += 1
        n = numero(match.group(0))
        return f"[{n}]" if n is not None else ""

    texto = _RE_GRUPO_IDS.sub(grupo, resposta.answer)
    texto = _RE_ID_SOLTO.sub(solto, texto)
    if not trocas:
        return resposta, 0
    texto = re.sub(r"[ \t]{2,}", " ", texto)
    texto = re.sub(r"\s+([.,;:])", r"\1", texto).strip()
    return resposta.model_copy(update={"answer": texto}), trocas


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
        or resposta.answer == _recusa(resposta.query, bases_consultadas, resposta.refusal_reason).answer
    ):
        return resposta, None
    motivo = resposta.refusal_reason.value
    return resposta.model_copy(update={
        "is_refusal": False, "refusal_reason": None, "confidence_level": ConfidenceLevel.BAIXA,
    }), motivo
