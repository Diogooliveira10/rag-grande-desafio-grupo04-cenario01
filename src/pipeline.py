"""Orquestra o pipeline completo: roteamento -> recuperação híbrida -> geração.

Une ``src/router.py``, ``src/retrieve.py`` e ``src/generate.py`` numa única
função compatível com o contrato ``PipelineFn`` de ``eval/run_eval.py``
(``Callable[[str], dict[str, Any]]``), para uso direto via::

    py .\\eval\\run_eval.py run --pipeline src.pipeline:responder

É o caso de uso "responder pergunta": conversa com a infraestrutura só pelas
portas de ``src/ports.py``, reunidas em ``Dependencias``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from src import structured_query
from src.obs import (Stage, bounded_log_value, lifecycle, new_query_id, new_run_id,
                     registrar, registrar_pulo, sessao)
from src.log_context import current_execution, execution, log_details
from src.conversation import resposta_conversacional, tentativa_de_injecao
from src.generate import ClienteLLM, gerar_resposta, pode_responder_sql
from src.ports import (Analisador, Buscador, ConsultaEstruturada, Conversa, Gerador,
                        Reformulador, Roteador)
from src.query_analysis import analisar_query, filtros_para_retrieve
from src.reformulation import Reformulacao, reformular_pergunta
from src.rerank import reranker_padrao
from src.retrieve import DEFAULT_TOP_K, RerankFn, RetrievalResult, filtro_opcional, retrieve
from src.router import inferir_motivo_recusa, rotear_pergunta
from src.schema import Chunk, ChunkMetadata
from src.structured_query import consultar_estruturado, pode_consultar_sql_direto

# Só as bases de escopo "oficial" no manifesto respondem perguntas; fixtures de
# teste e amostras experimentais ficam de fora da busca.
ESCOPOS_CONSULTADOS = ("oficial",)


@dataclass(frozen=True)
class Dependencias:
    """Implementações das portas (src/ports.py) que o pipeline usa.

    O pipeline só conversa com a infraestrutura por aqui. Para trocar uma peça,
    passe ``dataclasses.replace(dependencias_padrao(), buscar=minha_busca)`` a
    ``responder``.
    """
    analisar: Analisador
    rotear: Roteador
    buscar: Buscador
    consultar_sql: ConsultaEstruturada
    gerar: Gerador
    conversa: Conversa
    reformular: Reformulador
    reranker_padrao: Callable[[], Optional[RerankFn]]
    pode_consultar_sql_direto: Callable[..., bool]
    pode_responder_sql: Callable[[list[Chunk], Any], bool]
    tentativa_de_injecao: Callable[[str], bool]


def dependencias_padrao() -> Dependencias:
    """As implementações do projeto: ECOA/OpenRouter, FAISS + BM25, SQLite/Neon.

    Montadas a cada pergunta a partir dos nomes deste módulo, para um
    ``monkeypatch`` em ``pipeline.retrieve`` (como os testes fazem) continuar valendo.
    """
    return Dependencias(
        analisar=analisar_query, rotear=rotear_pergunta, buscar=retrieve,
        consultar_sql=consultar_estruturado, gerar=gerar_resposta, conversa=resposta_conversacional,
        reformular=reformular_pergunta, reranker_padrao=reranker_padrao,
        pode_consultar_sql_direto=pode_consultar_sql_direto, pode_responder_sql=pode_responder_sql,
        tentativa_de_injecao=tentativa_de_injecao,
    )


def _chunk_from_retrieval(result: RetrievalResult) -> Chunk:
    """Converte o resultado de retrieve.py no ``Chunk`` que generate.py espera."""
    return Chunk(content=result.content, metadata=ChunkMetadata(**result.metadata))


def _registrar_resumo(
    *,
    run_id: str,
    query_id: str,
    query: str,
    caminho: str,
    bases_roteadas: Sequence[Any],
    chunks: Sequence[Chunk],
    resposta: dict[str, Any],
) -> None:
    """Stage ``resumo_consulta``: fecha a pergunta no log dizendo se ela passou
    pela base SQL e pela base vetorial, com as evidências de cada uma e as
    fontes citadas. Os detalhes de cada etapa ficam nos stages anteriores."""
    sql = [c for c in chunks if c.metadata.chunk_id.startswith("sql:")]
    vetorial: dict[str, int] = {}
    for chunk in chunks:
        if not chunk.metadata.chunk_id.startswith("sql:"):
            vetorial[chunk.metadata.base_id] = vetorial.get(chunk.metadata.base_id, 0) + 1
    context = current_execution()
    etapas = context.stages() if context else {}
    sql_tentado = any(event["attempted"] for event in etapas.get(Stage.CONSULTA_SQL.value, []))
    vetorial_tentado = any(event["attempted"] for event in etapas.get(Stage.RETRIEVAL_DENSO.value, []))
    registrar(
        run_id=run_id,
        query_id=query_id,
        stage=Stage.RESUMO_CONSULTA,
        status="recusa" if resposta.get("is_refusal") else "ok",
        latencia_ms=0,
        input=query,
        output={
            "caminho": caminho,
            "bases_roteadas": [str(getattr(base, "value", base)) for base in bases_roteadas],
            "consultou_sql": sql_tentado or bool(sql),
            "consultou_vetorial": vetorial_tentado or bool(vetorial),
            "sql_retornou_evidencia": bool(sql),
            "vetorial_retornou_evidencia": bool(vetorial),
            "etapas": etapas,
            "degradado": any(event["outcome"] in {"erro", "degradado"}
                              for events in etapas.values() for event in events),
            "evidencias_sql": len(sql),
            "evidencias_vetorial": vetorial,
            "fontes_citadas": [fonte.get("chunk_id") for fonte in resposta.get("sources_used", [])],
            "is_refusal": bool(resposta.get("is_refusal")),
            "refusal_reason": resposta.get("refusal_reason"),
            "confidence_level": resposta.get("confidence_level"),
        },
    )


def responder(
    query: str,
    *,
    top_k: int = DEFAULT_TOP_K,
    reranker: Optional[RerankFn] = None,
    cliente: Optional[ClienteLLM] = None,
    run_id: Optional[str] = None,
    query_id: Optional[str] = None,
    escopos: Optional[Sequence[str]] = ESCOPOS_CONSULTADOS,
    session_id: Optional[str] = None,
    historico: Optional[Sequence[Any]] = None,
    dependencias: Optional[Dependencias] = None,
) -> dict[str, Any]:
    """Roda query_analysis + roteamento + recuperação híbrida + geração para uma pergunta.

    ``escopos=None`` consulta também as bases experimentais (ex.:
    ``exemplos_teste``), útil para desenvolver com um índice parcial.

    Quando o roteador não seleciona nenhuma base (recusa, fora de escopo ou
    "não identificado"), a recuperação é pulada; o motivo da recusa é inferido
    da justificativa do roteador (``router.inferir_motivo_recusa``) e repassado
    para ``gerar_resposta``, que senão cairia sempre em SEM_EVIDENCIA.

    A recuperação funde busca densa e BM25 (RRF) e reordena os primeiros
    candidatos com o cross-encoder de ``src/rerank.py``, quando ligado.

    ``query_analysis`` roda antes do roteamento e não influencia a decisão de
    quais bases consultar — só extrai filtros (ano, valor, órgão) que o
    ``retrieve`` aplica sobre os metadados dos chunks candidatos. Chunk sem o
    campo não é descartado, e se os filtros zerarem o resultado a busca é
    refeita sem eles.

    Perguntas exclusivamente analíticas sobre as bases estruturada/agregada
    tentam ``consulta_sql`` antes da recuperação. SQL suficiente dispensa busca
    e reranker; SQL indisponível/insuficiente preserva a busca como fallback.
    Perguntas mistas mantêm a recuperação e o resultado SQL antes dos chunks.

    ``run_id`` e ``query_id`` são repassados a todas as etapas, amarrando no
    log (``logs/pipeline/pipeline-AAAA-MM-DD.jsonl``) os stages de uma mesma pergunta.
    Passe ``query_id`` quando o chamador (ex.: ``app/api.py``, para o
    histórico em ``src/history_store.py``) precisa saber o id *antes* da
    resposta pronta; por padrão um novo id é gerado aqui.

    ``session_id`` liga as perguntas da mesma conversa: todo registro desta
    pergunta no log leva o id (``obs.sessao``), e ``src/trace.py`` monta com
    ele o caminho da conversa inteira. Sem ``session_id`` os registros ficam
    com ``session_id: null``.

    ``historico`` são as últimas trocas da conversa (``TurnoAnterior`` ou dicts
    com ``pergunta`` e ``resposta``), enviadas pelo cliente: o backend não guarda
    memória entre as perguntas. Quando a pergunta parece continuação ("os mesmos
    contratos", "E em 2024?"), ``src/reformulation.py`` a reescreve para se
    sustentar sozinha, e a pergunta reescrita substitui a original em todas as
    etapas. Nesse caso a resposta leva ``pergunta_reformulada``. Sem histórico,
    nada muda.

    ``dependencias`` troca as peças de infraestrutura (src/ports.py); sem ela,
    valem as do projeto (``dependencias_padrao``).
    """
    deps = dependencias or dependencias_padrao()
    run_id = run_id or new_run_id()
    query_id = query_id or new_query_id()
    with sessao(session_id), execution(run_id, query_id):
        with lifecycle(run_id=run_id, query_id=query_id, query=query,
                       start=Stage.PIPELINE_INICIO, end=Stage.PIPELINE_FIM,
                       extra={"top_k": top_k,
                              "escopos": list(escopos) if escopos is not None else None,
                              "cliente_injetado": cliente is not None,
                              "reranker_injetado": reranker is not None}) as flow:
            try:
                reformulacao = _reformular(query, historico, deps, run_id=run_id, query_id=query_id)
                resposta = _responder(
                    reformulacao.pergunta, top_k=top_k, reranker=reranker,
                    cliente=cliente, run_id=run_id, query_id=query_id, escopos=escopos, deps=deps,
                )
                if reformulacao.reescrita:
                    resposta = {**resposta, "query": query, "pergunta_reformulada": reformulacao.pergunta}
                flow.output = resposta
                etapas = current_execution().stages()
                degraded = any(event["outcome"] in {"erro", "degradado"}
                               for events in etapas.values() for event in events)
                flow.status = ("erro" if resposta.get("refusal_reason") == "erro_provedor"
                               else "recusa" if resposta.get("is_refusal")
                               else "degradado" if degraded else "ok")
                return resposta
            except BaseException:
                flow.status = "erro"
                raise
            finally:
                _registrar_etapas_ausentes(query, flow.status, run_id=run_id, query_id=query_id)


def _reformular(query: str, historico: Optional[Sequence[Any]], deps: Dependencias, *,
                run_id: str, query_id: str) -> Reformulacao:
    if historico and deps.tentativa_de_injecao(query):
        # Sem reformulação: o LLM reescrevia a injeção como a pergunta
        # anterior, e a resposta fixa da conversa não era dada.
        registrar_pulo(run_id=run_id, query_id=query_id, stage=Stage.REFORMULACAO,
                       query=query, motivo="tentativa_de_injecao")
        return Reformulacao(query, False, "tentativa_de_injecao")
    if historico:
        return deps.reformular(query, historico, run_id=run_id, query_id=query_id)
    registrar_pulo(run_id=run_id, query_id=query_id, stage=Stage.REFORMULACAO,
                   query=query, motivo="sem_historico")
    return Reformulacao(query, False, "sem_historico")


def _registrar_etapas_ausentes(query: str, status: str, *, run_id: str, query_id: str) -> None:
    """Etapas que não rodaram ficam explícitas no log, com o motivo.

    Assim uma etapa ausente não é confundida com um evento perdido. Em caso de
    falha, o envelope final registra o erro.
    """
    context = current_execution()
    observed = context.stages() if context else {}
    failed = status == "erro" or any(event["status"] == "erro"
                                     for events in observed.values() for event in events)
    for stage in (Stage.INTERACAO, Stage.REFORMULACAO, Stage.QUERY_ANALYSIS,
                  Stage.ROTEAMENTO, Stage.RETRIEVAL_DENSO, Stage.RETRIEVAL_BM25,
                  Stage.FUSAO_RRF, Stage.RERANK, Stage.CONSULTA_SQL,
                  Stage.GERACAO, Stage.VALIDACAO):
        if stage.value not in observed:
            registrar_pulo(run_id=run_id, query_id=query_id, stage=stage, query=query,
                           motivo="falha_anterior" if failed else "caminho_nao_requer_etapa")


def _registrar_interacao(query: str, conversa: Any, *, run_id: str, query_id: str) -> None:
    context = current_execution()
    if context and Stage.INTERACAO.value not in context.stages():
        registrar(run_id=run_id, query_id=query_id, stage=Stage.INTERACAO, status="ok",
                  latencia_ms=0, input=query, output={"tipo_interacao": "conversa" if conversa else "consulta"},
                  extra={"metodo": "regras", "llm_called": False})


def _responder_conversa(query: str, conversa: Any, *, run_id: str, query_id: str) -> dict[str, Any]:
    """Cumprimento, ajuda ou injeção: resposta fixa, sem consultar o acervo."""
    registrar(run_id=run_id, query_id=query_id, stage=Stage.GERACAO, status="conversa",
              latencia_ms=0, input=query, output="resposta de cortesia; sem consulta ao acervo",
              extra={"is_refusal": False, "num_sources": 0, "resposta_conversacional": True,
                     "resposta": {"answer": bounded_log_value(conversa.answer), "sources_used": [],
                                  "refusal_reason": None}})
    payload = conversa.model_dump(mode="json")
    _registrar_resumo(run_id=run_id, query_id=query_id, query=query, caminho="conversa",
                      bases_roteadas=[], chunks=[], resposta=payload)
    return payload


@dataclass
class _Coleta:
    """Evidências reunidas para a geração, e os filtros que a busca precisou ampliar."""
    chunks: list[Chunk] = field(default_factory=list)
    filtros_relaxados: dict[str, dict[str, list[str]]] = field(default_factory=dict)


class _Consulta:
    """O que as etapas de coleta compartilham numa pergunta: ids, recorte e reranker."""

    def __init__(self, deps: Dependencias, query: str, *, top_k: int, escopos: Optional[Sequence[str]],
                 reranker: Optional[RerankFn], run_id: str, query_id: str) -> None:
        self.deps, self.query, self.top_k, self.escopos = deps, query, top_k, escopos
        self.run_id, self.query_id = run_id, query_id
        self._reranker, self._reranker_resolvido = reranker, reranker is not None

    def buscar(self, pergunta: str, route: Any, filters: Optional[dict[str, Any]]) -> list[Any]:
        # Um cálculo SQL suficiente não precisa carregar o cross-encoder: ele só
        # é resolvido na primeira busca.
        if not self._reranker_resolvido:
            self._reranker = self.deps.reranker_padrao()
            self._reranker_resolvido = True
        return self.deps.buscar(pergunta, route, reranker=self._reranker, top_k=self.top_k,
                                filters=filters, escopos=self.escopos,
                                run_id=self.run_id, query_id=self.query_id)

    def consultar_sql(self, pergunta: str, filtros: Any, bases: Any, intencao: Any = None) -> list[Chunk]:
        extra = {"intencao": intencao} if intencao is not None else {}
        return self.deps.consultar_sql(pergunta, filtros, bases, run_id=self.run_id,
                                       query_id=self.query_id, **extra)

    def pular_busca(self) -> None:
        # Pula recuperação, não apresentação: o backend injeta os resultados
        # no marcador {{sql}} após uma única chamada de template à LLM.
        for stage in (Stage.RETRIEVAL_DENSO, Stage.RETRIEVAL_BM25, Stage.FUSAO_RRF, Stage.RERANK):
            registrar_pulo(run_id=self.run_id, query_id=self.query_id, stage=stage, query=self.query,
                           motivo="calculo_sql_suficiente")


def _coletar_por_intencoes(consulta: _Consulta, analise: Any, decisao: Any) -> _Coleta:
    """Pergunta com várias intenções: SQL e busca por intenção, intercaladas."""
    deps = consulta.deps
    coleta = _Coleta()
    grupos: list[list[Chunk]] = []
    for numero, intent in enumerate(analise.intencoes):
        route = {"bases": [b for b in intent.bases if b in decisao.bases_selecionadas]}
        with log_details(intencao=numero, tentativa_busca=1):
            grupo = None
            if deps.pode_consultar_sql_direto(intent.pergunta, intent.filtros, route["bases"], intencao=intent):
                grupo = consulta.consultar_sql(intent.pergunta, intent.filtros, route["bases"], intencao=intent)
            if grupo and deps.pode_responder_sql(grupo, route["bases"]):
                resultados = []
                consulta.pular_busca()
            else:
                resultados = consulta.buscar(intent.pergunta, route, filtros_para_retrieve(intent.filtros))
            if grupo is None:
                grupo = consulta.consultar_sql(intent.pergunta, intent.filtros, route["bases"], intencao=intent)
        calculo_disponivel = bool(grupo)
        grupo += [_chunk_from_retrieval(item) for item in resultados]
        for chunk in grupo:
            chunk.metadata.extras = {**chunk.metadata.extras, "intencao": numero,
                "total_intencoes": len(analise.intencoes),
                "subpergunta": intent.pergunta, "operacao": intent.operacao,
                "tipo_valor_solicitado": intent.filtros.tipo_valor,
                "tipo_medida_solicitada": intent.filtros.tipo_medida,
                "granularidade_solicitada": intent.filtros.granularidade,
                "calculo_disponivel": (
                    False if intent.filtros.tipo_valor == "unitario"
                    else calculo_disponivel if intent.operacao != "buscar" else None
                )}
            relaxed = chunk.metadata.extras.get("filtros_relaxados")
            if relaxed:
                coleta.filtros_relaxados[chunk.metadata.base_id] = relaxed
        grupos.append(grupo)
    coleta.chunks = _intercalar(grupos)
    return coleta


def _intercalar(grupos: list[list[Chunk]]) -> list[Chunk]:
    """Uma evidência de cada intenção por vez, SQL primeiro: nenhum pacote SQL
    elimina a outra intenção do orçamento do prompt. Repetidas saem."""
    for grupo in grupos:
        grupo.sort(key=lambda c: not c.metadata.chunk_id.startswith("sql:"))
    chunks: list[Chunk] = []
    vistos = set()
    while any(grupos):
        for grupo in grupos:
            if not grupo:
                continue
            chunk = grupo.pop(0)
            key = chunk.metadata.chunk_content_sha256 or chunk.metadata.chunk_id
            if key not in vistos:
                chunks.append(chunk)
                vistos.add(key)
    return chunks


def _coletar_consulta(consulta: _Consulta, analise: Any, decisao: Any) -> _Coleta:
    """Pergunta única: SQL primeiro; se não basta, busca (e busca sem filtros se zerou)."""
    deps, query, bases = consulta.deps, consulta.query, decisao.bases_selecionadas
    filtros = filtros_para_retrieve(analise.filtros)
    sql_chunks = None
    with log_details(tentativa_busca=1):
        if deps.pode_consultar_sql_direto(query, analise.filtros, bases):
            sql_chunks = consulta.consultar_sql(query, analise.filtros, bases)
        sql_suficiente = bool(sql_chunks) and deps.pode_responder_sql(sql_chunks, bases)
        if sql_suficiente:
            resultados = []
            consulta.pular_busca()
        else:
            resultados = consulta.buscar(query, decisao, filtros)
    if filtros and all(filtro_opcional(value) for value in filtros.values()) and not resultados and not sql_suficiente:
        # Os filtros são heurísticos; se zeraram tudo, a busca sem eles
        # ainda dá evidência ao generate (que decide se basta).
        with log_details(tentativa_busca=2, motivo="filtros_sem_resultado"):
            resultados = consulta.buscar(query, decisao, None)
    chunks = [_chunk_from_retrieval(item) for item in resultados]
    filtros_relaxados = {
        item.metadata["base_id"]: item.metadata.get("extras", {}).get("filtros_relaxados", {})
        for item in resultados if item.metadata.get("extras", {}).get("filtros_relaxados")
    }
    # Ranking/estatística/comparação: o resultado SQL vai à frente, para
    # caber no orçamento do prompt antes dos trechos da busca.
    if sql_chunks is None:
        sql_chunks = consulta.consultar_sql(query, analise.filtros, bases)
    for chunk in sql_chunks + chunks:
        chunk.metadata.extras = {**chunk.metadata.extras,
            "calculo_disponivel": (
                False if analise.filtros.tipo_valor == "unitario"
                else bool(sql_chunks) if structured_query.requer_calculo(query) else None
            ),
            "tipo_valor_solicitado": analise.filtros.tipo_valor,
            "tipo_medida_solicitada": analise.filtros.tipo_medida,
            "granularidade_solicitada": analise.filtros.granularidade}
    return _Coleta(chunks=sql_chunks + chunks, filtros_relaxados=filtros_relaxados)


_ROTULOS_DE_FILTRO = {"data_vigencia": "ano/período de vigência", "orgao": "órgão", "valor": "valor",
                      "esfera": "esfera", "data_assinatura": "data de assinatura",
                      "data_resultado": "data do resultado", "data_publicacao": "data de publicação",
                      "ano_pca": "ano do PCA"}
_ROTULOS_DE_BASE = {"contratos_estruturado": "contratos", "atas_conversacional": "atas",
                    "editais_normativo": "normas", "pca_agregado": "indicadores"}


def _aviso_filtros_relaxados(filtros_relaxados: dict[str, dict[str, list[str]]]) -> str:
    """Aviso de que a busca foi ampliada. Fica em ``avisos``, fora do texto da
    resposta: é sobre a busca, não sobre o que foi perguntado."""
    notas = []
    for base, motivos in sorted(filtros_relaxados.items()):
        for motivo, chaves in sorted(motivos.items()):
            campos = ", ".join(_ROTULOS_DE_FILTRO.get(chave, chave) for chave in chaves)
            por_que = "metadados ausentes" if motivo == "sem_campo" else "nenhum resultado com o recorte solicitado"
            notas.append(f"{_ROTULOS_DE_BASE.get(base, base)}: {campos} ({por_que})")
    return ("Nenhum resultado atendeu a todos os filtros da pergunta; a busca foi ampliada em "
            + "; ".join(notas) + ".")


def _responder(
    query: str,
    *,
    top_k: int,
    reranker: Optional[RerankFn],
    cliente: Optional[ClienteLLM],
    run_id: str,
    query_id: str,
    escopos: Optional[Sequence[str]],
    deps: Dependencias,
) -> dict[str, Any]:
    conversa = deps.conversa(query, run_id=run_id, query_id=query_id)
    _registrar_interacao(query, conversa, run_id=run_id, query_id=query_id)
    if conversa is not None:
        return _responder_conversa(query, conversa, run_id=run_id, query_id=query_id)

    analise = deps.analisar(query, run_id=run_id, query_id=query_id)
    # A análise vai sempre: o roteador usa as intenções quando existem e o
    # fornecedor/CNPJ reconhecido para incluir a base estruturada.
    decisao = deps.rotear(query, run_id=run_id, query_id=query_id, analise=analise)
    consulta = _Consulta(deps, query, top_k=top_k, escopos=escopos, reranker=reranker,
                         run_id=run_id, query_id=query_id)
    refusal_reason_hint = None
    if decisao.bases_selecionadas and analise.intencoes:
        coleta = _coletar_por_intencoes(consulta, analise, decisao)
    elif decisao.bases_selecionadas:
        coleta = _coletar_consulta(consulta, analise, decisao)
    else:
        coleta = _Coleta()
        refusal_reason_hint = inferir_motivo_recusa(decisao.justificativa)

    resposta = deps.gerar(query, coleta.chunks, decisao.bases_selecionadas, cliente=cliente,
                          run_id=run_id, query_id=query_id, refusal_reason_hint=refusal_reason_hint)
    if coleta.filtros_relaxados:
        resposta.avisos.append(_aviso_filtros_relaxados(coleta.filtros_relaxados))
    payload = resposta.model_dump(mode="json")
    if not decisao.bases_selecionadas:
        caminho = "recusa_sem_consulta"
    else:
        caminho = "consulta_por_intencoes" if analise.intencoes else "consulta"
    _registrar_resumo(run_id=run_id, query_id=query_id, query=query, caminho=caminho,
                      bases_roteadas=decisao.bases_selecionadas, chunks=coleta.chunks, resposta=payload)
    return payload


def responder_incluindo_experimentais(query: str) -> dict[str, Any]:
    """``responder`` sem o recorte de escopo, no contrato ``PipelineFn`` do eval::

        py .\\eval\\run_eval.py run --pipeline src.pipeline:responder_incluindo_experimentais
    """
    return responder(query, escopos=None)


__all__ = ["Dependencias", "dependencias_padrao", "responder", "responder_incluindo_experimentais"]
