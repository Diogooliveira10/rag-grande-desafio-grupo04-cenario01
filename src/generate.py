from __future__ import annotations

import time
import uuid
from typing import Any, Optional, Protocol

import config
from src.log_evidence import auditar_geracao, auditoria_completa
from src.obs import TokenUsage, token_usage_from_response
from src.schema import Chunk, ConfidenceLevel, Natureza, RAGResponse, RefusalReason
from src.sensitivity import filtrar_chunks_permitidos, mascarar, mascarar_chunks

# As partes da geração moram em src/generation/ desde 2026-10-27; todos os nomes
# continuam importáveis daqui (from src.generate import ..., gen.X).
from src.generation.guardrails import (  # noqa: F401
    _PADROES_GUARDRAIL,
    _checar_guardrail_previo,
)
from src.generation.context import (  # noqa: F401
    _CONVERSA_DESCARTADOS,
    _CONVERSA_NO_FIM,
    _CONVERSA_ROTULOS_DA_MENSAGEM,
    _CORTE,
    _DATA_E_HORA_RE,
    _VAZIOS,
    _cabecalho,
    _chunks_no_orcamento,
    _compactar,
    _dividir_entre_mensagens,
    _modalidade_vinculada,
    _natureza_label,
    _representar_conversa,
    _representar_registro,
    _sem_vazios,
)
from src.generation.notices import (  # noqa: F401
    _AVISO_FONTE_SINTETICA,
    _AVISO_RESSALVA,
    _NIVEIS,
    _RESSALVA_DE_INSUFICIENCIA,
    _aplicar_recorte,
    _avisar_fonte_sintetica,
    _avisar_limites_da_evidencia,
    _confianca_do_recorte,
    _confianca_dos_recortes,
    _limitar_confianca_pela_ressalva,
    _recorte_dos_chunks,
    _sem_acento,
)
from src.generation.responses import (  # noqa: F401
    _extrair_json,
    _fonte_do_chunk,
    _recusa,
)
from src.generation.sql_answer import (  # noqa: F401
    _MARCADOR_SQL,
    _MARCA_SQL_SEM_REGISTROS,
    _NUMEROS_NO_TEMPLATE,
    _PROMPT_TEMPLATE_SQL,
    _TEXTO_SQL_SEM_REGISTROS,
    _linhas_para_exibicao,
    _linhas_sql_reais,
    _resposta_sql_deterministica,
    _sql_sem_registros,
    _validar_template_sql,
    pode_responder_sql,
)
from src.generation.parsing import (  # noqa: F401
    _ALIASES_CHAVES,
    _CONFIANCA_ALTA,
    _CONFIANCA_MEDIA,
    _campo,
    _nivel_de_confianca,
    _parsear_resposta_llm,
)
from src.generation.generation_log import (  # noqa: F401
    _LIMITE_DE_TEXTO_NO_LOG,
    _logar_evento,
    _logar_validacao,
    _prompt_para_log,
    _texto_para_log,
)
from src.generation.citations import (  # noqa: F401
    _ID_INTERNO,
    _PEDIDO_DE_CITACAO,
    _RECUSAS_CONVERTIVEIS,
    _RE_E_SEM_SEPARADOR,
    _RE_GRUPO_IDS,
    _RE_ID_SOLTO,
    _ROTULO_ID,
    _SEP_ID,
    _converter_recusa_indevida,
    _falta_citar,
    _incluir_trechos_mencionados,
    _numerar_citacoes,
)


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
            mensagens, diagnosticos=diagnosticos, temperature=config.LLM_TEMPERATURE,
            response_format=({"type": "json_schema", "json_schema": {"name": "query_plan", "schema": schema}}
                             if schema is not None else {"type": "json_object"}),
        )

    _chamar.diagnosticos = diagnosticos
    return _chamar


def _texto_e_tokens(saida: Any, *, extra: dict[str, Any] | None = None) -> tuple[str, TokenUsage]:
    """Aceita ``str`` ou SDK; texto sem usage tem consumo explicitamente desconhecido."""
    tokens = token_usage_from_response(saida, extra=extra)
    if isinstance(saida, str):
        return saida, tokens
    return saida.choices[0].message.content or "", tokens


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
relação com a pergunta (sem_evidencia). Evidência insuficiente não é recusa: havendo trechos \
relacionados, mesmo que não respondam tudo, responda com is_refusal=false. Quando a pergunta \
pede para reconhecer a insuficiência, reconhecê-la é a resposta, não uma recusa.

Com evidência parcial, responda (is_refusal=false) o que os trechos mostram, cite os trechos \
examinados, diga o que falta e use confiança baixa ou media. Nesse caso, declare a lacuna numa \
frase explícita, como "os dados são \
insuficientes para ..." ou "não há dados suficientes para ...", em vez de só insinuá-la; \
quando a pergunta pede itens que atendam a um critério e nenhum atende, diga "nenhum ... \
atende" com essas palavras. Se a evidência responde a pergunta inteira, não acrescente \
ressalva de insuficiência. Trecho "sql:" é evidência: use seus números; se diz que nenhum \
registro atende, responda isso citando-o.

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
A modalidade de um registro vem só do campo de modalidade dele (modalidade_licitacao ou
modalidade= no cabeçalho, que pode vir da compra vinculada). Não a deduza do amparo legal,
que é a norma citada, nem a copie de registro de outro objeto ou processo; sem esse campo,
a modalidade daquele registro não está informada.
Não associe regras de dispensa a um pregão ou de outro processo ao registro em análise.
Relacione cada norma à modalidade explicitamente identificada pela fonte.
Cláusula que prevê atraso, penalidade ou cancelamento não comprova ocorrência desses
eventos. Para afirmar ocorrência, cite relato, decisão ou registro do fato.
Conclusão de ausência também se apoia no contexto: ao dizer que os trechos não mostram
algo, cite os trechos que você examinou, no texto e em sources_used. Se a pergunta
depende do que falta (comparar, calcular, confirmar), diga também, com essas palavras,
que os dados são insuficientes para isso (ex.: "os dados são insuficientes para comparar
as compras").
Ao citar uma regra de norma, nomeie a norma e o dispositivo como aparecem no trecho
(ex.: "Lei Complementar 123/2006, art. 44", "Decreto 48.816/2023, art. 5º") e use os
termos dela (ex.: "desempate", "tratamento diferenciado").
Valor global/estimado/contratado não é preço unitário nem pagamento realizado.
Zero registrado é zero, não ausência de informação. Quando houver valorGlobal=0
e valorInicial positivo, informe os dois campos com seus nomes e significados distintos.
Se tipo_valor_solicitado=unitario e calculo_disponivel=false, uma listagem pode
confirmar a existência do contrato, mas não informa preço por veículo. Informe
essa insuficiência e que a mediana unitária não pode ser calculada; nunca use o
valor total do contrato, inclusive R$ 0,00, como preço por veículo.
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
Trechos com sintetico=true são e-mails e conversas fictícios. Pergunta sobre o que um
e-mail ou conversa diz se responde com o trecho sintético: relate o conteúdo, cite o
evidence_id e avise que o cenário é fictício; recusar ou dizer que falta evidência é erro.
Não use conteúdo sintético para comprovar fatos reais, pagamentos ou ocorrências do PNCP.
Em conversas, a data do e-mail, mensagem ou cabeçalho "Data da conversa" é a data
DA COMUNICAÇÃO, não a data da entrega, pagamento ou execução mencionada no texto.
Para local/data/horário de recebimento, priorize a mensagem que CONFIRMA o agendamento
ou autoriza a operação, não o pedido inicial de confirmação. Preserve detalhes explícitos
como subsolo, dia e horário. Agendamento autorizado não comprova entrega já realizada.
Snapshot principalmente de 2025 não confirma oportunidades abertas hoje.
Datas em cabeçalhos de impressão, captura ou acesso não são datas de publicação.
Só afirme publicação quando o campo ou texto identificar explicitamente essa data.
Se uma modalidade vier da compra vinculada no cabeçalho, informe essa proveniência;
a ausência no registro do contrato não anula a modalidade da compra vinculada.
Não confunda ausência nos trechos recuperados com ausência no corpus inteiro.
Prefira resposta direta, listas ou tabelas curtas; valores monetários em reais (R$)
e datas legíveis. Não exponha identificadores E1/E2 fora de citações entre colchetes.
Toda resposta, inclusive recusa, precisa de texto não vazio.
"""


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


def _gerar_com_template_sql(
    query: str, chunks: list[Chunk], resposta_sql: RAGResponse, *, cliente: Optional[ClienteLLM],
    run_id: str, query_id: str, inicio: float, sensibilidade: dict[str, Any],
) -> RAGResponse:
    """Uma chamada de apresentação; números/fontes/confiança nunca vêm do modelo.

    Não usa engine de templates nem executa o conteúdo de {{sql}}. Apenas uma
    substituição literal, após a validação, preserva todas as partes calculadas.
    A narrativa é orientada a ser neutra; a validação sintática não é prova
    semântica de ausência de toda afirmação indevida.
    """
    mensagens = [
        {"role": "system", "content": _PROMPT_TEMPLATE_SQL},
        {"role": "user", "content": "Pergunta (apenas para apresentação):\n" + mascarar(query)[0]},
    ]
    fontes = [s.chunk_id for s in resposta_sql.sources_used]
    sensibilidade.update({
        "llm_called": False, "chunks_no_prompt": 0, "chunks_fora_do_orcamento": 0,
        "prompt_chunk_ids": [], "prompt_evidence_ids": {}, "evidencias_no_prompt": [],
        "evidencias_usadas": fontes, "sql_injetado_chunk_ids": fontes,
        # Conteúdo completo em Markdown, mesmo quando o resumo answer é cortado.
        "resultado_sql_markdown": mascarar(resposta_sql.answer)[0],
        "prompt": _prompt_para_log(mensagens),
        "prompt_truncated": any(len(str(m["content"])) > _LIMITE_DE_TEXTO_NO_LOG for m in mensagens),
    })
    tokens, fallback, erro_parsing = None, None, None
    template = _MARCADOR_SQL
    try:
        cliente = cliente or _cliente_padrao({
            "type": "object", "additionalProperties": False, "required": ["answer"],
            # Alguns decodificadores do provedor removem o marcador quando a
            # string usa minLength/maxLength. O limite é validado localmente;
            # enviar só o tipo preserva {{sql}} na saída do modelo real.
            "properties": {"answer": {"type": "string"}},
        })
        sensibilidade["llm_called"] = True
        bruto, tokens = _texto_e_tokens(cliente(mensagens), extra=sensibilidade)
    except Exception as exc:
        fallback = "erro_provedor"
        sensibilidade["erro"] = f"{type(exc).__name__}: {mascarar(str(exc))[0]}"
    inicio_validacao = time.monotonic()
    if fallback is None:
        try:
            template = _validar_template_sql(bruto)
        except (ValueError, TypeError, AttributeError) as exc:
            fallback = "template_invalido"
            erro_parsing = f"{type(exc).__name__}: {exc}"
            sensibilidade["template_erro"] = erro_parsing
    # O bloco SQL só é inserido aqui: nunca foi enviado ao modelo nem truncado
    # pelo orçamento do prompt. Substituição não recursiva, sem Jinja/eval.
    answer, mascaramentos_resposta = mascarar(template.replace(_MARCADOR_SQL, resposta_sql.answer, 1))
    avisos = list(resposta_sql.avisos)
    if fallback:
        avisos.append("Não foi possível gerar a apresentação pela LLM; exibindo o resultado SQL calculado diretamente.")
        sensibilidade["fallback_motivo"] = fallback
    resposta = resposta_sql.model_copy(update={"answer": answer, "avisos": avisos})
    metodo = "sql_template_fallback" if fallback else "sql_template"
    sensibilidade["llm"] = dict(getattr(cliente, "diagnosticos", {}))
    sensibilidade["template_sql"] = template
    if auditoria_completa():
        usados = [c for c in chunks if c.metadata.chunk_id in fontes]
        sensibilidade["auditoria_evidencias"] = auditar_geracao(chunks, usados, metodo=metodo, mensagens=mensagens)
    _logar_evento(run_id, query_id, "degradado" if fallback else "ok", inicio, resposta,
                  metodo=metodo, tokens=tokens, **sensibilidade)
    _logar_validacao(run_id, query_id, inicio_validacao, resposta, erro_parsing=erro_parsing,
                    citacoes_descartadas=[], chaves_alias=[], mascaramentos_resposta=mascaramentos_resposta)
    return resposta


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

    resposta_sql = _resposta_sql_deterministica(query, chunks, bases_consultadas)
    if resposta_sql is not None and config.SQL_APRESENTACAO_LLM:
        return _gerar_com_template_sql(
            query, chunks, resposta_sql, cliente=cliente, run_id=run_id,
            query_id=query_id, inicio=inicio, sensibilidade=sensibilidade,
        )
    if resposta_sql is not None:
        # Padrão: a resposta do SQL sai sem LLM. A apresentação pelo LLM
        # (SQL_APRESENTACAO_LLM) levava as consultas ao vivo de ~2,5 s para
        # 7-25 s e as fazia depender do ECOA, para acrescentar só uma frase.
        return _responder_pelo_sql(resposta_sql, chunks, run_id=run_id, query_id=query_id,
                                   inicio=inicio, sensibilidade=sensibilidade)

    chunks, mensagens = _preparar_prompt(query, chunks, bases_consultadas, sensibilidade)
    cliente = cliente or _cliente_padrao(_schema_resposta([f"E{i}" for i in range(1, len(chunks) + 1)]))

    sensibilidade["llm_called"] = True
    sensibilidade["uso_tokens"] = {"status": "indisponivel", "fonte": "response.usage"}
    try:
        bruto, tokens = _texto_e_tokens(cliente(mensagens), extra=sensibilidade)
    except Exception as exc:
        return _recusa_por_erro_do_provedor(exc, cliente, query, bases_consultadas, run_id=run_id,
                                            query_id=query_id, inicio=inicio, sensibilidade=sensibilidade)

    llm_info = dict(getattr(cliente, "diagnosticos", {}))
    sensibilidade["llm"] = llm_info
    inicio_validacao = time.monotonic()
    resposta, erro_parsing, chaves_alias, citacoes_descartadas = _parsear_resposta_llm(
        bruto, query, chunks, bases_consultadas
    )
    # A recusa indevida ("fora de escopo" com texto útil) vira resposta mais
    # adiante; a falta de fonte é conferida já na forma convertida (P4.3).
    convertida, _ = _converter_recusa_indevida(resposta, bases_consultadas, erro_parsing)
    if _falta_citar(convertida, chunks, erro_parsing):
        resposta, tokens = _pedir_citacao(cliente, mensagens, bruto, resposta, tokens,
                                          query, chunks, bases_consultadas, sensibilidade)
        llm_info = sensibilidade["llm"] = dict(getattr(cliente, "diagnosticos", {})) or llm_info
    if erro_parsing:
        sensibilidade["erro_parsing"] = erro_parsing
    if chaves_alias:
        sensibilidade["chaves_fora_do_contrato"] = chaves_alias
    resposta, recusa_convertida, mascaramentos_resposta = _validar_resposta_do_llm(
        resposta, chunks, bases_consultadas, erro_parsing, sensibilidade)
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


def _recusa_por_erro_do_provedor(exc: Exception, cliente: ClienteLLM, query: str, bases_consultadas: list[Natureza],
                                 *, run_id: str, query_id: str, inicio: float,
                                 sensibilidade: dict[str, Any]) -> RAGResponse:
    """LLM indisponível (ECOA e reserva): recusa ``erro_provedor``, com o erro no log."""
    llm_info = dict(getattr(cliente, "diagnosticos", {}))
    sensibilidade["llm"] = llm_info
    resposta = _recusa(query, bases_consultadas, RefusalReason.ERRO_PROVEDOR)
    _logar_evento(
        run_id, query_id, "erro_llm", inicio, resposta,
        modelo=llm_info.get("model", GENERATION_MODEL), erro=f"{type(exc).__name__}: {exc}", **sensibilidade,
    )
    return resposta


def _pedir_citacao(cliente: ClienteLLM, mensagens: list[dict[str, Any]], bruto: str, resposta: RAGResponse,
                   tokens: TokenUsage, query: str, chunks: list[Chunk], bases_consultadas: list[Natureza],
                   sensibilidade: dict[str, Any]) -> tuple[RAGResponse, TokenUsage]:
    """Uma nova chamada pedindo as citações; fica a resposta nova só se vier com fontes.

    O prompt já exige citar ao menos uma evidência, e o LLM às vezes conclui
    "os trechos não tratam disso" sem citar os trechos (P4.3 do golden). A
    retentativa nunca piora: resposta inválida, recusa ou ainda sem fontes
    mantém a original. ``sensibilidade["retentativa_citacao"]`` vai para o log.
    """
    registro: dict[str, Any] = {"motivo": "resposta_sem_fontes"}
    sensibilidade["retentativa_citacao"] = registro
    try:
        bruto_novo, tokens_novos = _texto_e_tokens(cliente([
            *mensagens,
            {"role": "assistant", "content": bruto},
            {"role": "user", "content": _PEDIDO_DE_CITACAO},
        ]))
    except Exception as exc:  # a original continua valendo
        registro["resultado"] = f"erro: {type(exc).__name__}"
        return resposta, tokens
    total = TokenUsage(prompt=tokens.prompt + tokens_novos.prompt,
                       completion=tokens.completion + tokens_novos.completion,
                       total=tokens.total + tokens_novos.total)
    nova, erro, _, _ = _parsear_resposta_llm(bruto_novo, query, chunks, bases_consultadas)
    if erro or nova.is_refusal or not nova.sources_used:
        registro["resultado"] = "sem_mudanca"
        return resposta, total
    registro["resultado"] = "citou"
    registro["fontes"] = [fonte.chunk_id for fonte in nova.sources_used]
    return nova, total


def _responder_pelo_sql(resposta_sql: RAGResponse, chunks: list[Chunk], *, run_id: str, query_id: str,
                        inicio: float, sensibilidade: dict[str, Any]) -> RAGResponse:
    """Resposta determinística do SQL, sem LLM, mascarada e registrada no log."""
    answer, mascaramentos_resposta = mascarar(resposta_sql.answer)
    resposta_sql = resposta_sql.model_copy(update={"answer": answer})
    fontes_sql = [source.chunk_id for source in resposta_sql.sources_used]
    if auditoria_completa():
        usados = [c for c in chunks if c.metadata.chunk_id in fontes_sql]
        sensibilidade["auditoria_evidencias"] = auditar_geracao(chunks, usados, metodo="sql_deterministico")
    _logar_evento(run_id, query_id, "ok", inicio, resposta_sql,
                  metodo="sql_deterministico", llm_called=False,
                  evidencias_usadas=fontes_sql, chunks_no_prompt=0,
                  chunks_fora_do_orcamento=0, prompt_chunk_ids=[], prompt=[],
                  **sensibilidade)
    _logar_validacao(run_id, query_id, time.monotonic(), resposta_sql,
                    erro_parsing=None, citacoes_descartadas=[], chaves_alias=[],
                    mascaramentos_resposta=mascaramentos_resposta)
    return resposta_sql


def _preparar_prompt(query: str, chunks: list[Chunk], bases_consultadas: list[Natureza],
                     sensibilidade: dict[str, Any]) -> tuple[list[Chunk], list[dict[str, Any]]]:
    """Evidências que cabem no orçamento e as mensagens do prompt.

    Anota em ``sensibilidade`` (o extra do log) o que entrou e o que ficou de fora.
    """
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
    mensagens = _montar_prompt(query, chunks_prompt)
    if auditoria_completa():
        sensibilidade["auditoria_evidencias"] = auditar_geracao(
            chunks, chunks_prompt, metodo="llm", mensagens=mensagens)
    sensibilidade["prompt"] = _prompt_para_log(mensagens)
    sensibilidade["prompt_truncated"] = any(
        len(mascarar(str(message.get("content", "")))[0]) > _LIMITE_DE_TEXTO_NO_LOG
        for message in mensagens
    )
    return chunks_prompt, mensagens


def _validar_resposta_do_llm(resposta: RAGResponse, chunks: list[Chunk], bases_consultadas: list[Natureza],
                             erro_parsing: Optional[str],
                             sensibilidade: dict[str, Any]) -> tuple[RAGResponse, Any, Any]:
    """Recusa indevida, citações [n], política de confiança, avisos e mascaramento.

    Devolve a resposta, a recusa convertida (se houve) e os mascaramentos da resposta.
    """
    resposta, recusa_convertida = _converter_recusa_indevida(resposta, bases_consultadas, erro_parsing)
    if recusa_convertida:
        sensibilidade["recusa_convertida"] = recusa_convertida
    resposta, ids_no_texto = _numerar_citacoes(resposta, chunks)
    if ids_no_texto:
        sensibilidade["ids_internos_no_texto"] = ids_no_texto
    resposta = _aplicar_recorte(resposta, chunks)
    resposta = _limitar_confianca_pela_ressalva(resposta)
    resposta = _avisar_fonte_sintetica(resposta, chunks)
    resposta = _avisar_limites_da_evidencia(resposta, chunks)
    # Defesa em profundidade: o dado pode ter vindo da própria pergunta.
    answer_mascarada, mascaramentos_resposta = mascarar(resposta.answer)
    if mascaramentos_resposta:
        resposta = resposta.model_copy(update={"answer": answer_mascarada})
        sensibilidade["mascaramentos_resposta"] = mascaramentos_resposta
    return resposta, recusa_convertida, mascaramentos_resposta
