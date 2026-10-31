"""Configuração tipada e imutável do projeto, montada uma vez a partir do ambiente.

``carregar(ambiente, raiz)`` é uma função pura: recebe as variáveis (o
``os.environ`` depois do ``.env``, ou um dicionário num teste) e devolve uma
``Configuracao`` dividida por responsabilidade. Quem precisa só do rerank recebe
só ``ConfiguracaoRerank``, e não um módulo global inteiro.

``config.py`` continua expondo os mesmos nomes de antes (``config.OLLAMA_URL``),
lidos desta configuração: o código existente não muda. A injeção nos módulos é
o passo seguinte, um de cada vez (seção 10.1 do guia da versão Java).

Chaves e senhas são ``Segredo``: o valor é usado normalmente, mas não aparece
quando a configuração é impressa num log.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

_VERDADEIRO = {"1", "true", "sim", "yes"}


class Segredo(str):
    """Texto que não aparece no ``repr``: ``print(configuracao)`` não vaza a chave."""

    def __repr__(self) -> str:
        return "Segredo('***')" if self else "Segredo('')"


class _Ambiente:
    def __init__(self, variaveis: Mapping[str, str]) -> None:
        self._variaveis = variaveis

    def texto(self, nome: str, padrao: str) -> str:
        return self._variaveis.get(nome, padrao).strip()

    def booleano(self, nome: str, padrao: str) -> bool:
        return self.texto(nome, padrao).lower() in _VERDADEIRO

    def inteiro(self, nome: str, padrao: str) -> int:
        return int(self.texto(nome, padrao))

    def decimal(self, nome: str, padrao: str) -> float:
        return float(self.texto(nome, padrao))

    def segredo(self, nome: str, padrao: str = "") -> Segredo:
        return Segredo(self.texto(nome, padrao))


# Orçamento do contexto da geração (src/generate.py). O Ollama da turma usa
# janela de 2048 tokens e corta o INÍCIO do prompt que passar disso, levando
# junto a instrução de responder em JSON; para essa janela curta, 3500 chars
# deixam o prompt em torno de 1.500 tokens, com folga para a resposta. O
# OpenRouter tem janela grande e o orçamento precisa comportar o top-k inteiro:
# 3500 chars descartavam 7 dos 10 chunks recuperados e o modelo declarava
# "ausente" o que estava no topo do ranking (defeito achado na P2.2).
ORCAMENTO_CONTEXTO_JANELA_CURTA = 3500
ORCAMENTO_CONTEXTO_OPENROUTER = 12000
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


@dataclass(frozen=True)
class ConfiguracaoLlm:
    # Endpoint e modelos compartilhados da turma.
    ollama_url: str
    ollama_api_key: Segredo
    generation_model: str
    model_min_interval_seconds: float
    openrouter_api_key: Segredo
    openrouter_base_url: str
    # Modelo preferido das chamadas OpenRouter (geração/reformulação). O padrão
    # preserva o comportamento anterior: sem configurar, só o "openrouter/free" é
    # usado. A demo define explicitamente um modelo nomeado (ex. MiMo-V2.6-Flash) e
    # mantém o free como reserva quando ele estiver indisponível.
    openrouter_model: str
    # Reserva usada somente quando o modelo preferido está indisponível (rede,
    # timeout, 404/408/429/5xx). Erros de entrada não trocam de modelo.
    openrouter_model_fallback: str
    openrouter_max_tokens: int
    # Prazo total de cada chamada ao OpenRouter, em segundos. O timeout do SDK é
    # entre pacotes, e o OpenRouter manda sinais de "processando" enquanto gera:
    # no golden de 20/10 uma chamada levou 277 s com timeout de 60 s.
    openrouter_timeout: float
    provider_mode: str
    # Orçamento do prompt (ver ORCAMENTO_CONTEXTO_*): o padrão depende do provedor.
    generation_context_chars: int
    generation_chunk_chars: int
    # Tempo máximo (s) de cada chamada de geração e quantas vezes tentar de novo.
    # A geração normal leva de 20 a 90 s no servidor da turma.
    generation_timeout: float
    # Prazo próprio de cada reformulação: a etapa reescreve consultas curtas e não
    # deve competir com a geração pelo mesmo timeout global. Nos logs de outubro,
    # 91% das reescritas do ECOA terminam em até 60 s (com 15 s, só 43%: o resto
    # caía no fallback e a P4.3 falhava); o prazo corta a cauda, que chegou a 149 s.
    reformulacao_timeout: float
    generation_max_retries: int
    # Temperatura das chamadas com resposta em JSON (geração e análise da pergunta).
    # 0 = a mesma pergunta com as mesmas evidências tende ao mesmo texto; com 0,2 a
    # redação variava entre execuções e mudava o resultado do golden set.
    temperature: float
    # ``reasoning_effort`` mandado ao LLM do ECOA. Vazio = padrão do servidor (o
    # Qwen3 pensa antes de responder). ``none`` desliga o raciocínio: medido em
    # 2026-10-16, a mesma pergunta caiu de 15 s para 2 s. Só o ECOA recebe; o
    # OpenRouter usa o padrão de cada modelo.
    reasoning_effort: str
    # Resposta que sai do SQL redigida pelo LLM (só a apresentação; números e
    # fontes continuam do SQL). Desligado por padrão: medido em 2026-10-19, as
    # consultas ao vivo iam de ~2,5 s para 7-25 s e passavam a depender do ECOA.
    sql_apresentacao_llm: bool


@dataclass(frozen=True)
class ConfiguracaoEmbeddings:
    # Endpoint compartilhado da turma (o mesmo do LLM) e o modelo de embedding.
    ecoa_url: str
    ecoa_api_key: Segredo
    model: str
    # Respiro entre lotes de embedding quando o encoder é o endpoint compartilhado
    # (ECOA). O modo direto usa isto antes de cair no encoder local.
    pausa_entre_lotes: float
    # Provedor dos embeddings de CONSULTA (src/retrieve.py). Vale os mesmos nomes
    # de src.create_embeddings._provider_defaults: ecoa (padrão, endpoint
    # compartilhado da turma), ollama (llama.cpp/Ollama local), openai e custom.
    # Só muda quem embeda a PERGUNTA; os vetores dos documentos são os do índice e
    # não são reembedados por causa disso. A chave do cache de query inclui endpoint
    # e modelo, então vetores de um provedor não valem para o outro.
    provider: str
    ollama_local_url: str
    ollama_local_api_key: Segredo
    ollama_local_model: str
    # Presets comuns OpenAI-compatible. Todos usam o mesmo contrato
    # ``POST /v1/embeddings``; o endpoint e o modelo podem ser sobrescritos.
    openai_url: str
    openai_api_key: Segredo
    openai_model: str
    custom_url: str
    custom_api_key: Segredo
    custom_model: str
    # Consulta somente: não muda o encoder nem regrava vetores do índice.
    query_timeout: float
    query_cooldown: float
    dense_retrieval_enabled: bool

    def endpoint(self, provider: Optional[str] = None) -> tuple[str, str, str]:
        """``(base_url, api_key, model)`` do provedor pedido ou do configurado."""
        nome = (provider or self.provider).lower()
        if nome == "shared":
            nome = "ecoa"
        presets = {
            "ecoa": (self.ecoa_url, self.ecoa_api_key, self.model),
            "ollama": (self.ollama_local_url, self.ollama_local_api_key, self.ollama_local_model),
            "openai": (self.openai_url, self.openai_api_key, self.openai_model),
            "custom": (self.custom_url, self.custom_api_key, self.custom_model),
        }
        if nome not in presets:
            raise ValueError("EMBEDDING_PROVIDER deve ser ecoa, ollama, openai ou custom")
        url, chave, modelo = presets[nome]
        return url, str(chave), modelo


@dataclass(frozen=True)
class ConfiguracaoBusca:
    # Rerank (src/rerank.py). "local" (padrão) = cross-encoder local (RERANK_MODEL
    # aceita qualquer CrossEncoder do sentence-transformers); "ecoa" = endpoint
    # /api/rerank do servidor da turma, com o local de reserva. Na ablação de
    # 2026-10-07 o local acertou mais (docs/ABLACAO_RERANK.md).
    rerank_enabled: bool
    rerank_provider: str
    rerank_url: str
    rerank_timeout: float
    rerank_model: str
    # O perfil experimental de 50 candidatos aumenta recall, mas ainda tem
    # regressões de ranking; ativar explicitamente, não como mudança global.
    rerank_candidates: int
    # Variedade na busca: no máximo N trechos da mesma compra (ata) à frente dos
    # candidatos do rerank; o excedente vai para o fim, não é descartado. 0 desliga.
    max_per_purchase: int
    rerank_max_length: int
    # Carregar o cross-encoder quando a API sobe, e não na primeira pergunta:
    # o carregamento leva ~15 s (69 s se ainda precisar baixar o modelo) e o
    # rerank em si menos de 1 s (medido em 2026-10-14).
    rerank_preload: bool
    # Ler os índices (vectors.jsonl, FAISS e documents.jsonl) quando a API sobe:
    # a busca guarda o que leu, e a primeira pergunta deixa de pagar o carregamento.
    index_preload: bool
    # Aviso de conferência na origem; nunca remove/corrige valores do snapshot.
    value_review_threshold: float


@dataclass(frozen=True)
class ConfiguracaoColeta:
    # Parâmetros de rede usados pela coleta e ingestão.
    request_timeout: float
    request_attempts: int
    # OCR de páginas de PDF sem texto (src/ocr.py). TESSERACT_CMD vazio procura o
    # executável no PATH e no local padrão do instalador do Windows.
    tesseract_cmd: str
    ocr_lang: str
    ocr_dpi: int


@dataclass(frozen=True)
class Caminhos:
    root_dir: Path
    raw_data_dir: Path
    normalized_data_dir: Path
    index_dir: Path
    logs_dir: Path
    pipeline_logs_dir: Path
    maintenance_logs_dir: Path
    backups_dir: Path
    test_temp_dir: Path
    # Manifesto de um bundle imutável de runtime. Vazio mantém o comportamento
    # existente e deixa readiness analítico explicitamente não pronto.
    snapshot_manifest: str
    # Base estruturada em SQL (src/structured_db.py). Vazio = SQLite local em
    # data/structured/; preenchido = Postgres (ex.: Neon). Nunca versionar o valor.
    database_url: Segredo


@dataclass(frozen=True)
class ConfiguracaoApi:
    # API HTTP (app/api.py): chave administrativa para consultar histórico e traces
    # de outras sessões. Sem ela, os dois exigem X-Session-Id do dono.
    rag_api_key: Segredo
    # true = histórico geral (perguntas de todos) aberto sem chave, para uma demo
    # que precise dele. Padrão fechado.
    history_public: bool
    # false = sem /docs, /redoc e /openapi.json: com o backend exposto pelo túnel,
    # o esquema da API deixa de ficar público. Padrão ligado, para uso local.
    api_docs: bool
    # Limite por cliente (app/rate_limit.py), em requisições por minuto; 0 desliga.
    # Perguntas e feedback: cada pergunta pelo LLM ocupa o ECOA por 20 a 100 s.
    # Leitura (histórico, trace, explorar): o site faz várias chamadas por tela.
    rate_limit_query_per_minute: int
    rate_limit_read_per_minute: int


@dataclass(frozen=True)
class ConfiguracaoLog:
    # Cadeia de hashes no log JSONL (src/log_chain.py): cada linha guarda o hash da
    # anterior. Com a chave, HMAC-SHA256; sem ela, SHA-256.
    chain_enabled: bool
    chain_key: Segredo
    # Dias que o histórico (src/history_store.py) guarda cada consulta; 0 = sem expurgo.
    history_retention_days: int
    # Os arquivos diários do pipeline seguem a retenção do histórico por padrão; 0 guarda todos.
    log_retention_days: int
    maintenance_retention_days: int
    # Apenas o detalhamento das evidências; não altera o orçamento enviado ao LLM.
    evidence_detail: str


@dataclass(frozen=True)
class Configuracao:
    llm: ConfiguracaoLlm
    embeddings: ConfiguracaoEmbeddings
    busca: ConfiguracaoBusca
    coleta: ConfiguracaoColeta
    caminhos: Caminhos
    api: ConfiguracaoApi
    log: ConfiguracaoLog


def carregar(variaveis: Mapping[str, str], raiz: Path) -> Configuracao:
    """Monta a configuração a partir das variáveis de ambiente; erro de valor inválido sai aqui."""
    ambiente = _Ambiente(variaveis)
    return Configuracao(
        llm=_llm(ambiente),
        embeddings=_embeddings(ambiente),
        busca=_busca(ambiente),
        coleta=ConfiguracaoColeta(
            request_timeout=ambiente.decimal("REQUEST_TIMEOUT", "30"),
            request_attempts=ambiente.inteiro("REQUEST_ATTEMPTS", "3"),
            tesseract_cmd=ambiente.texto("TESSERACT_CMD", ""),
            ocr_lang=ambiente.texto("OCR_LANG", "por"),
            ocr_dpi=ambiente.inteiro("OCR_DPI", "300"),
        ),
        caminhos=_caminhos(ambiente, raiz),
        api=ConfiguracaoApi(
            rag_api_key=ambiente.segredo("RAG_API_KEY"),
            history_public=ambiente.booleano("HISTORY_PUBLIC", "false"),
            api_docs=ambiente.booleano("API_DOCS", "true"),
            rate_limit_query_per_minute=ambiente.inteiro("RATE_LIMIT_QUERY_PER_MINUTE", "10"),
            rate_limit_read_per_minute=ambiente.inteiro("RATE_LIMIT_READ_PER_MINUTE", "120"),
        ),
        log=_log(ambiente),
    )


def _llm(ambiente: _Ambiente) -> ConfiguracaoLlm:
    provider_mode = ambiente.texto("LLM_PROVIDER_MODE", "auto")
    orcamento_padrao = (ORCAMENTO_CONTEXTO_OPENROUTER if provider_mode == "openrouter"
                        else ORCAMENTO_CONTEXTO_JANELA_CURTA)
    return ConfiguracaoLlm(
        ollama_url=ambiente.texto("OLLAMA_URL", "http://ecoa-llm.duckdns.org:11434/v1"),
        ollama_api_key=ambiente.segredo("OLLAMA_API_KEY", "ollama"),
        generation_model=ambiente.texto("GENERATION_MODEL", "qcwind/qwen3-8b-instruct-Q4-K-M"),
        model_min_interval_seconds=ambiente.decimal("MODEL_MIN_INTERVAL_SECONDS", "10"),
        openrouter_api_key=ambiente.segredo("OPENROUTER_API_KEY"),
        openrouter_base_url=OPENROUTER_BASE_URL,
        openrouter_model=ambiente.texto("OPENROUTER_MODEL", "openrouter/free"),
        openrouter_model_fallback=ambiente.texto("OPENROUTER_MODEL_FALLBACK", "openrouter/free"),
        openrouter_max_tokens=ambiente.inteiro("OPENROUTER_MAX_TOKENS", "4096"),
        openrouter_timeout=ambiente.decimal("OPENROUTER_TIMEOUT", "60"),
        provider_mode=provider_mode,
        generation_context_chars=ambiente.inteiro("GENERATION_CONTEXT_CHARS", str(orcamento_padrao)),
        generation_chunk_chars=ambiente.inteiro("GENERATION_CHUNK_CHARS", "900"),
        generation_timeout=ambiente.decimal("GENERATION_TIMEOUT", "180"),
        reformulacao_timeout=ambiente.decimal("REFORMULACAO_TIMEOUT", "60"),
        generation_max_retries=ambiente.inteiro("GENERATION_MAX_RETRIES", "1"),
        temperature=ambiente.decimal("LLM_TEMPERATURE", "0"),
        reasoning_effort=ambiente.texto("GENERATION_REASONING_EFFORT", ""),
        sql_apresentacao_llm=ambiente.booleano("SQL_APRESENTACAO_LLM", "false"),
    )


def _embeddings(ambiente: _Ambiente) -> ConfiguracaoEmbeddings:
    modelo = ambiente.texto("EMBEDDING_MODEL", "xagi/qwen3-embedding:0.6b-q4_k_m")
    return ConfiguracaoEmbeddings(
        ecoa_url=ambiente.texto("OLLAMA_URL", "http://ecoa-llm.duckdns.org:11434/v1"),
        ecoa_api_key=ambiente.segredo("OLLAMA_API_KEY", "ollama"),
        model=modelo,
        pausa_entre_lotes=ambiente.decimal("PAUSA_ENTRE_LOTES_EMBEDDING", "0.5"),
        provider=ambiente.texto("EMBEDDING_PROVIDER", "ecoa").lower(),
        ollama_local_url=ambiente.texto("OLLAMA_LOCAL_URL", "http://localhost:11434/v1"),
        ollama_local_api_key=ambiente.segredo("OLLAMA_LOCAL_API_KEY", "ollama"),
        ollama_local_model=ambiente.texto("OLLAMA_LOCAL_EMBEDDING_MODEL", modelo),
        openai_url=ambiente.texto("OPENAI_API_URL", "https://api.openai.com/v1"),
        openai_api_key=ambiente.segredo("OPENAI_API_KEY"),
        openai_model=ambiente.texto("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"),
        custom_url=ambiente.texto("CUSTOM_API_URL", ""),
        custom_api_key=ambiente.segredo("CUSTOM_API_KEY"),
        custom_model=ambiente.texto("CUSTOM_EMBEDDING_MODEL", modelo),
        query_timeout=max(0.1, ambiente.decimal("QUERY_EMBEDDING_TIMEOUT", "8")),
        query_cooldown=max(0.0, ambiente.decimal("QUERY_EMBEDDING_COOLDOWN", "30")),
        dense_retrieval_enabled=ambiente.booleano("DENSE_RETRIEVAL_ENABLED", "true"),
    )


def _busca(ambiente: _Ambiente) -> ConfiguracaoBusca:
    ollama_url = ambiente.texto("OLLAMA_URL", "http://ecoa-llm.duckdns.org:11434/v1")
    return ConfiguracaoBusca(
        rerank_enabled=ambiente.booleano("RERANK_ENABLED", "true"),
        rerank_provider=ambiente.texto("RERANK_PROVIDER", "local").lower(),
        rerank_url=ambiente.texto("RERANK_URL", "") or ollama_url.rstrip("/").removesuffix("/v1") + "/api/rerank",
        rerank_timeout=ambiente.decimal("RERANK_TIMEOUT", "30"),
        rerank_model=ambiente.texto("RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"),
        rerank_candidates=ambiente.inteiro("RERANK_CANDIDATES", "10"),
        max_per_purchase=ambiente.inteiro("RETRIEVAL_MAX_PER_PURCHASE", "2"),
        rerank_max_length=ambiente.inteiro("RERANK_MAX_LENGTH", "512"),
        rerank_preload=ambiente.booleano("RERANK_PRELOAD", "true"),
        index_preload=ambiente.booleano("INDEX_PRELOAD", "true"),
        value_review_threshold=max(1.0, ambiente.decimal("VALUE_REVIEW_THRESHOLD", "1000000000")),
    )


def _caminhos(ambiente: _Ambiente, raiz: Path) -> Caminhos:
    logs = raiz / "logs"
    return Caminhos(
        root_dir=raiz,
        raw_data_dir=raiz / "data" / "raw" / "v1",
        normalized_data_dir=raiz / "data" / "normalized",
        index_dir=raiz / "index",
        logs_dir=logs,
        pipeline_logs_dir=logs / "pipeline",
        maintenance_logs_dir=logs / "maintenance",
        backups_dir=raiz / "data" / "backups",
        test_temp_dir=raiz / ".tmp" / "tests",
        snapshot_manifest=ambiente.texto("SNAPSHOT_MANIFEST", ""),
        database_url=ambiente.segredo("DATABASE_URL"),
    )


def _log(ambiente: _Ambiente) -> ConfiguracaoLog:
    retencao_do_historico = ambiente.inteiro("HISTORY_RETENTION_DAYS", "30")
    retencao_do_log = ambiente.inteiro("LOG_RETENTION_DAYS", str(retencao_do_historico))
    retencao_da_manutencao = ambiente.inteiro("MAINTENANCE_RETENTION_DAYS", "14")
    detalhe = ambiente.texto("LOG_EVIDENCE_DETAIL", "resumo").lower()
    if detalhe not in {"resumo", "completo"}:
        raise ValueError("LOG_EVIDENCE_DETAIL deve ser resumo ou completo")
    return ConfiguracaoLog(
        chain_enabled=ambiente.booleano("LOG_CHAIN_ENABLED", "true"),
        chain_key=ambiente.segredo("LOG_CHAIN_KEY"),
        history_retention_days=retencao_do_historico,
        log_retention_days=retencao_do_log,
        maintenance_retention_days=retencao_da_manutencao,
        evidence_detail=detalhe,
    )
