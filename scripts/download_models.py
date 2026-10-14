'''Baixa os modelos externos do pipeline para uma máquina nova.

Dois artefatos vivem fora do Git e são necessários para reproduzir a
configuração de defesa do projeto (ver docs/registro_execucao_v6.md):

1. O cross-encoder do rerank (config.RERANK_MODEL, ~470 MB) — normalmente
   baixado para o cache do Hugging Face na primeira inferência; aqui o
   download é forçado de propósito.
2. O GGUF Q8_0 do Qwen3-Embedding-0.6B (639 MB) — pesos dos embeddings de
   CONSULTA no modo local (EMBEDDING_PROVIDER=ollama + llama-server). A URL é
   a oficial da Qwen e o SHA-256 verificado é o mesmo do checkpoint de
   compatibilidade registrado em docs/registro_execucao_v6.md.

Uso:
    python scripts/download_models.py                  # tudo
    python scripts/download_models.py --only reranker
    python scripts/download_models.py --only gguf --dest D:\\LLM
    python scripts/download_models.py --skip-sha       # sem verificação
'''

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

# Fonte oficial: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF
GGUF_REPOSITORIO = "Qwen/Qwen3-Embedding-0.6B-GGUF"
GGUF_ARQUIVO = "Qwen3-Embedding-0.6B.Q8_0.gguf"
# Checkpoint de compatibilidade do índice v6 (docs/registro_execucao_v6.md).
GGUF_SHA256 = "e40d4a708d0b324fc133bae547bd64dec79edb885410ef262c1e5c2efb2050b3"


def _sha256(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def baixar_reranker() -> None:
    import config
    from sentence_transformers import CrossEncoder

    print(f"[reranker] baixando {config.RERANK_MODEL} para o cache do Hugging Face...")
    CrossEncoder(config.RERANK_MODEL)
    print("[reranker] pronto.")


def baixar_gguf(dest: Path, verificar_sha: bool = True) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    alvo = dest / GGUF_ARQUIVO
    if alvo.exists():
        print(f"[gguf] já presente: {alvo}")
    else:
        print(f"[gguf] baixando {GGUF_REPOSITORIO}/{GGUF_ARQUIVO} (~639 MB)...")
        try:
            from huggingface_hub import hf_hub_download

            baixado = hf_hub_download(
                GGUF_REPOSITORIO, GGUF_ARQUIVO, local_dir=str(dest)
            )
            alvo = Path(baixado)
        except ImportError:
            import urllib.request

            url = (
                "https://huggingface.co/"
                + GGUF_REPOSITORIO
                + "/resolve/main/"
                + GGUF_ARQUIVO
            )

            def _progresso(blocos, tamanho, total):
                pronto = blocos * tamanho
                if total:
                    pct = 100 * pronto // total
                    sys.stdout.write(f"\r[gguf] {pct:3d}% ({pronto // (1 << 20)} MB)")
                    sys.stdout.flush()

            urllib.request.urlretrieve(url, alvo, reporthook=_progresso)
            print()
    if verificar_sha:
        obtido = _sha256(alvo)
        if obtido != GGUF_SHA256:
            raise SystemExit(
                f"[gguf] SHA-256 divergente.\n  esperado: {GGUF_SHA256}\n"
                f"  obtido:   {obtido}\n"
                "  O índice da v6 foi compatibilizado com este checkpoint. Se você"
                " sabe o que está fazendo, use --skip-sha."
            )
        print(f"[gguf] SHA-256 verificado: {obtido}")
    print(alvo)
    return alvo


def imprimir_comando_servidor(gguf: Path) -> None:
    print(
        "\nPara subir o servidor de embeddings local (llama.cpp b10578 ou mais"
        " novo, build com Vulkan/GPU opcional):\n\n"
        f"  llama-server -m {gguf} --embedding --pooling last "
        "--host 127.0.0.1 --port 18321 -c 4096 -np 1 -b 16 -ub 2048 -t 2\n\n"
        "E no .env:\n\n"
        "  EMBEDDING_PROVIDER=ollama\n"
        "  OLLAMA_LOCAL_URL=http://127.0.0.1:18321/v1\n\n"
        "Proveniência e medições: docs/registro_execucao_v6.md."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("reranker", "gguf"), default=None)
    parser.add_argument(
        "--dest",
        type=Path,
        default=RAIZ / ".models",
        help="destino do GGUF (padrão: <repo>/.models)",
    )
    parser.add_argument("--skip-sha", action="store_true")
    args = parser.parse_args()

    if args.only in (None, "reranker"):
        baixar_reranker()
    if args.only in (None, "gguf"):
        gguf = baixar_gguf(args.dest, verificar_sha=not args.skip_sha)
        imprimir_comando_servidor(gguf)


if __name__ == "__main__":
    main()
