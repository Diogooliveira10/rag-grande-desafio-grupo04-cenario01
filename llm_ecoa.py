from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as _PrazoDaThread

from openai import APIConnectionError, APIStatusError, OpenAI

import config

from config import (
    EMBEDDING_MODEL,
    GENERATION_MODEL,
    OLLAMA_API_KEY,
    OLLAMA_URL,
)

# Cliente OpenAI apontando para o servidor Ollama
client = OpenAI(
    base_url=OLLAMA_URL,
    api_key=OLLAMA_API_KEY,
)


def erro_de_disponibilidade(error: Exception) -> bool:
    """Só indisponibilidade de transporte/serviço permite trocar o provedor."""
    if isinstance(error, (APIConnectionError, ConnectionError, TimeoutError)):
        return True
    return isinstance(error, APIStatusError) and (
        error.status_code in {404, 408, 429} or error.status_code >= 500
    )


def _resposta_vazia_do_provedor(resposta) -> bool:
    """Resposta sem texto ou com ``finish_reason == "error"``."""
    escolhas = getattr(resposta, "choices", None) or []
    if not escolhas:
        return False
    conteudo = getattr(getattr(escolhas[0], "message", None), "content", None) or ""
    return getattr(escolhas[0], "finish_reason", None) == "error" or not conteudo.strip()


def _com_prazo(chamada, segundos: float):
    """Executa ``chamada`` com prazo total de relógio; estourado, TimeoutError.

    A thread abandonada termina sozinha quando a resposta chegar; o pipeline
    não espera por ela.
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="openrouter")
    futuro = executor.submit(chamada)
    try:
        return futuro.result(timeout=segundos)
    except _PrazoDaThread:
        raise TimeoutError(f"OpenRouter sem resposta em {segundos:.0f} s") from None
    finally:
        executor.shutdown(wait=False)


def completar_chat(mensagens: list[dict], *, response_format: dict | None = None,
                   diagnosticos: dict | None = None, **parametros):
    """Modo auto: ECOA com fallback Free; modo openrouter: sem chamadas ao LLM ECOA."""
    from src.obs import aguardar_modelo
    from src.model_observation import observar_chamada

    info = diagnosticos if diagnosticos is not None else {}
    info.update({"provider": "ecoa", "model_requested": GENERATION_MODEL,
                 "model": GENERATION_MODEL, "fallback_used": False,
                 "llm_called": False, "chamadas_modelo": [],
                 "uso_tokens": {"status": "nao_aplicavel"}})
    arguments = {"messages": mensagens, "temperature": 0.2, **parametros}
    if response_format is not None:
        arguments["response_format"] = response_format
    info["mode"] = config.LLM_PROVIDER_MODE

    def _openrouter():
        if not config.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY não configurada")
        fallback = OpenAI(base_url=config.OPENROUTER_BASE_URL, api_key=config.OPENROUTER_API_KEY,
                          timeout=config.OPENROUTER_TIMEOUT, max_retries=0)
        fallback_arguments = {**arguments, "max_tokens": min(arguments.get("max_tokens", config.OPENROUTER_MAX_TOKENS), config.OPENROUTER_MAX_TOKENS),
                              "extra_body": {"provider": {"require_parameters": True}}}
        # Modelo preferido e, só se ele estiver indisponível, a reserva. Quando
        # forem o mesmo, uma única chamada; a ordem e o modelo efetivo ficam em
        # chamadas_modelo, para a proveniência não depender do nome pedido.
        modelos = [config.OPENROUTER_MODEL]
        if config.OPENROUTER_MODEL_FALLBACK != config.OPENROUTER_MODEL:
            modelos.append(config.OPENROUTER_MODEL_FALLBACK)
        ultimo_erro: Exception | None = None
        for indice, modelo in enumerate(modelos):
            info.update({"provider": "openrouter", "model_requested": modelo, "model": modelo})
            if indice:
                info["fallback_used"] = True
            aguardar_modelo()
            try:
                resposta = observar_chamada(
                    lambda modelo=modelo: _com_prazo(
                        lambda: fallback.chat.completions.create(model=modelo, **fallback_arguments),
                        config.OPENROUTER_TIMEOUT),
                    provider="openrouter", model=modelo,
                    sdk_max_retries=0, diagnosticos=info)
                # HTTP 200 sem texto e com finish_reason "error": o provedor
                # falhou no meio da geração (visto em 2026-10-21). Conta como
                # indisponibilidade e passa para a reserva, se ainda houver.
                if _resposta_vazia_do_provedor(resposta) and indice < len(modelos) - 1:
                    info["fallback_error_type"] = "resposta_vazia_do_provedor"
                    continue
                return resposta
            except Exception as fallback_error:
                ultimo_erro = fallback_error
                info["fallback_error_type"] = type(fallback_error).__name__
                # Mesma regra do caminho ECOA: só indisponibilidade troca de
                # modelo, e nunca se já estivermos na reserva.
                if not erro_de_disponibilidade(fallback_error) or indice == len(modelos) - 1:
                    raise
        raise ultimo_erro  # inalcançável: o laço sempre retorna ou relança
    if config.LLM_PROVIDER_MODE == "openrouter":
        response = _openrouter()
    elif config.LLM_PROVIDER_MODE == "auto":
        aguardar_modelo()
        primary = client.with_options(
            timeout=config.GENERATION_TIMEOUT, max_retries=config.GENERATION_MAX_RETRIES
        ) if hasattr(client, "with_options") else client
        argumentos_ecoa = dict(arguments)
        if config.GENERATION_REASONING_EFFORT:
            argumentos_ecoa["extra_body"] = {**argumentos_ecoa.get("extra_body", {}),
                                             "reasoning_effort": config.GENERATION_REASONING_EFFORT}
            info["reasoning_effort"] = config.GENERATION_REASONING_EFFORT
        try:
            response = observar_chamada(
                lambda: primary.chat.completions.create(model=GENERATION_MODEL, **argumentos_ecoa),
                provider="ecoa", model=GENERATION_MODEL,
                sdk_max_retries=config.GENERATION_MAX_RETRIES, diagnosticos=info)
        except Exception as error:
            if not erro_de_disponibilidade(error) or not config.OPENROUTER_API_KEY:
                raise
            info.update({"fallback_used": True, "primary_error_type": type(error).__name__,
                         "primary_status_code": getattr(error, "status_code", None)})
            response = _openrouter()
    else:
        raise ValueError("LLM_PROVIDER_MODE deve ser auto ou openrouter")
    info["model"] = getattr(response, "model", None) or info["model_requested"]
    choices = getattr(response, "choices", [])
    if choices:
        info["finish_reason"] = getattr(choices[0], "finish_reason", None)
        info["content_chars"] = len(getattr(choices[0].message, "content", None) or "")
    details = getattr(getattr(response, "usage", None), "completion_tokens_details", None)
    info["reasoning_tokens"] = getattr(details, "reasoning_tokens", None)
    return response


def _normalizar_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def gerar_resposta(prompt: str) -> str:
    """Exemplo simples de geração de texto usando o LLM."""
    response = completar_chat(
        [{"role": "user", "content": prompt}],
        max_tokens=300,
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def gerar_embedding(texto: str) -> list[float]:
    """Exemplo simples de geração de embedding vetorial para um texto."""
    from src.model_observation import observar_chamada
    response = observar_chamada(lambda: client.embeddings.create(model=EMBEDDING_MODEL, input=texto),
        provider="ecoa", model=EMBEDDING_MODEL, sdk_max_retries=getattr(client, "max_retries", None))
    return response.data[0].embedding


def _client_for(base_url: str, api_key: str) -> OpenAI:
    return OpenAI(base_url=_normalizar_base_url(base_url), api_key=api_key)


def gerar_embeddings(
    textos: list[str],
    *,
    base_url: str = OLLAMA_URL,
    api_key: str = OLLAMA_API_KEY,
    model: str = EMBEDDING_MODEL,
) -> list[list[float]]:
    """Gera embeddings em lote, preservando a ordem dos textos enviados."""
    if not textos:
        return []
    normalized_base_url = _normalizar_base_url(base_url)
    active_client = client if normalized_base_url == _normalizar_base_url(OLLAMA_URL) else _client_for(normalized_base_url, api_key)
    from src.model_observation import observar_chamada
    response = observar_chamada(lambda: active_client.embeddings.create(model=model, input=textos),
        provider="openai_compativel", model=model, sdk_max_retries=getattr(active_client, "max_retries", None))
    ordered = sorted(response.data, key=lambda item: getattr(item, "index", 0))
    return [item.embedding for item in ordered]


if __name__ == "__main__":
    print("=== Exemplo de Geração de Texto (LLM) ===")
    prompt_exemplo = "O que é Retrieval-Augmented Generation (RAG)? Responda em duas frases."
    print(f"Prompt: {prompt_exemplo}")
    resposta = gerar_resposta(prompt_exemplo)
    print(f"Resposta:\n{resposta}\n")

    print("=== Exemplo de Embeddings ===")
    texto_exemplo = "RAG combina recuperação de informação e modelos de linguagem."
    print(f"Texto: {texto_exemplo}")
    vetor = gerar_embedding(texto_exemplo)
    print(f"Dimensão do vetor gerado: {len(vetor)}")
    print(f"Primeiros 5 valores: {vetor[:5]}...\n")
