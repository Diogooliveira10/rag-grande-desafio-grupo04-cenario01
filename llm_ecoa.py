from openai import OpenAI

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


def _normalizar_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def gerar_resposta(prompt: str) -> str:
    """Exemplo simples de geração de texto usando o LLM."""
    response = client.chat.completions.create(
        model=GENERATION_MODEL,
        messages=[
            {"role": "user", "content": prompt}
        ],
        max_tokens=300,
        temperature=0.2,
    )
    return response.choices[0].message.content or ""


def gerar_embedding(texto: str) -> list[float]:
    """Exemplo simples de geração de embedding vetorial para um texto."""
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texto,
    )
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
    response = active_client.embeddings.create(
        model=model,
        input=textos,
    )
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
