"""Run the existing embedding pipeline against a local llama.cpp GGUF model.

Use ``--smoke-test`` to check the local model without writing embedding files.
Use ``--run`` to process the configured JSONL corpus and write the normal
embedding cache and report through :mod:`src.create_embeddings`.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# The local llama.cpp server does not check API keys, but the existing OpenAI
# client rejects an empty key. Keep the placeholder process-local and respect
# an explicitly configured key.
os.environ.setdefault("CUSTOM_API_KEY", "local")

from src.create_embeddings import DEFAULT_INPUT, DEFAULT_OUTPUT, _content_sha256, main as create_embeddings_main
from llm_ecoa import gerar_embeddings
from src.index import file_sha256


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL_PATH = Path(r"D:\LLM\Qwen3-Embedding-0.6B.Q8_0.gguf")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 18321
DEFAULT_CONTEXT_SIZE = 4096
DEFAULT_THREADS = 4
SMOKE_TEST_TEXT = "Teste local de geração de embedding."
COMPATIBILITY_QUERIES = (
    "Qual foi o valor médio por quilo de arroz contratado por municípios do RJ em 2025?",
    "Quais exigências do edital de contratação de ambulâncias?",
    "O que as atas de registro de preços dizem sobre fornecedores?",
    "Qual o valor total planejado no PCA de 2025 por unidade?",
)


def compare_query_vectors(
    local: list[list[float]], remote: list[list[float]], documents: list[list[float]]
) -> dict[str, Any]:
    """Critérios do projeto, sobre a mesma amostra de documentos locais."""
    from src.retrieve import _cosine

    vectors = local + remote + documents
    if len(local) != len(remote) or not local or len(documents) < 5:
        raise ValueError("amostra de compatibilidade insuficiente")
    dimension = len(local[0])
    if any(len(vector) != dimension or not vector or not all(math.isfinite(v) for v in vector)
           or not any(vector) for vector in vectors):
        raise ValueError("vetores incompatíveis, não finitos ou com norma nula")
    cosines, overlaps = [], []
    for left, right in zip(local, remote):
        cosines.append(_cosine(left, right))
        local_rank = sorted(range(len(documents)), key=lambda i: (-_cosine(left, documents[i]), i))[:5]
        remote_rank = sorted(range(len(documents)), key=lambda i: (-_cosine(right, documents[i]), i))[:5]
        overlaps.append(len(set(local_rank) & set(remote_rank)) / 5)
    return {
        "dimension": dimension, "cosines": cosines, "top5_overlaps": overlaps,
        "mean_top5_overlap": sum(overlaps) / len(overlaps),
        "thresholds": {"min_query_cosine": 0.95, "mean_top5_overlap": 0.8},
        "compatible": min(cosines) >= 0.95 and sum(overlaps) / len(overlaps) >= 0.8,
    }


def _check_compatibility(args: argparse.Namespace, base_url: str) -> int:
    import config
    from urllib.request import Request, urlopen
    from src.retrieve import embedding_query_text

    report: dict[str, Any] = {
        "status": "started", "local_checkpoint": str(args.model_path.resolve()),
        "local_checkpoint_sha256": file_sha256(args.model_path), "local_backend": "llama.cpp",
        "remote_model": config.EMBEDDING_MODEL, "remote_backend": "ECOA/Ollama",
        "pooling": "last", "context_size": args.context_size, "threads": args.threads,
        "batch_size": 1, "pause_seconds": 0, "queries": list(COMPATIBILITY_QUERIES),
        "gpu_layers": args.gpu_layers, "parallel": args.parallel,
        "server_context_size": args.context_size * args.parallel,
        "ubatch_size": args.ubatch_size, "flash_attention": args.flash_attn,
        "query_instruction": embedding_query_text("").removesuffix("Query:"),
        "document_instruction": None, "documents": [],
    }
    report_path = args.report or PROJECT_ROOT / "eval" / "runs" / "embedding-compatibility-v5.json"
    try:
        if config.EMBEDDING_MODEL != "xagi/qwen3-embedding:0.6b-q4_k_m":
            raise ValueError("ECOA deve usar o modelo de embeddings configurado pelo desafio")
        show_url = config.OLLAMA_URL.rstrip("/").removesuffix("/v1") + "/api/show"
        request = Request(show_url, data=json.dumps({"model": config.EMBEDDING_MODEL}).encode(),
                          headers={"Content-Type": "application/json", "Authorization": f"Bearer {config.OLLAMA_API_KEY}"})
        with urlopen(request, timeout=30) as response:
            info = json.load(response)
        report["remote_details"] = info.get("details")
        report["remote_model_info"] = info.get("model_info")
        identity = json.dumps(info.get("model_info", {})).casefold()
        if "qwen3" not in identity or "embedding" not in identity:
            raise ValueError("metadados ECOA não confirmam checkpoint Qwen3-Embedding")
        sample = []
        official = {"contratos_estruturado", "editais_normativo", "atas_conversacional", "pca_agregado"}
        counts: dict[str, int] = {}
        with args.input.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                base = record.get("base_id")
                text = record.get("text", "")
                if base in official and counts.get(base, 0) < 5 and text.strip():
                    _validate_token_limit(base_url, text, args.context_size)
                    sample.append(text)
                    counts[base] = counts.get(base, 0) + 1
                    report["documents"].append({"chunk_id": record["chunk_id"], "base_id": base,
                                                "source_file": record.get("source_file"),
                                                "text_sha256": _content_sha256(text)})
        document_vectors = [_request_embedding(base_url, args.model_name, text) for text in sample]
        local, remote = [], []
        for query in COMPATIBILITY_QUERIES:
            encoded = embedding_query_text(query)
            _validate_token_limit(base_url, encoded, args.context_size)
            local.append(_request_embedding(base_url, args.model_name, encoded))
            started = time.perf_counter()
            remote.append(gerar_embeddings([encoded], model=config.EMBEDDING_MODEL)[0])
            report.setdefault("remote_calls", []).append({"latency_ms": (time.perf_counter() - started) * 1000})
        report.update(compare_query_vectors(local, remote, document_vectors))
        report["status"] = "passed" if report["compatible"] else "failed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


def _resolve_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return str(candidate.resolve())
    resolved = shutil.which(value)
    if resolved:
        return resolved
    raise FileNotFoundError(
        f"llama.cpp não encontrado: {value!r}. Informe --llama-exe ou adicione llama.exe ao PATH."
    )


def _check_port(port: int) -> None:
    with socket.socket() as listener:
        try:
            listener.bind((DEFAULT_HOST, port))
        except OSError as error:
            raise RuntimeError(f"a porta local {port} já está em uso") from error


def _server_log(log_file: Any, limit: int = 5000) -> str:
    log_file.flush()
    log_file.seek(0, 2)
    end = log_file.tell()
    log_file.seek(max(0, end - limit))
    return log_file.read().decode("utf-8", errors="replace").strip()


def _wait_for_server(
    process: subprocess.Popen[bytes], base_url: str, log_file: Any, timeout: float = 90
) -> None:
    health_url = base_url.removesuffix("/v1") + "/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            details = _server_log(log_file)
            raise RuntimeError(
                f"llama.cpp encerrou durante a inicialização (código {process.returncode}).\n{details}"
            )
        try:
            from urllib.request import urlopen

            with urlopen(health_url, timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    details = _server_log(log_file)
    raise TimeoutError(f"llama.cpp não iniciou em {timeout:.0f}s.\n{details}")


def _request_embedding(base_url: str, model: str, text: str) -> list[float]:
    vectors = gerar_embeddings(
        [text],
        base_url=base_url,
        api_key=os.environ.get("CUSTOM_API_KEY", "local"),
        model=model,
    )
    if not vectors or not isinstance(vectors[0], list) or not vectors[0]:
        raise RuntimeError("llama.cpp retornou um embedding vazio")
    if not all(math.isfinite(value) for value in vectors[0]) or not any(vectors[0]):
        raise RuntimeError("llama.cpp retornou vetor não finito ou norma nula")
    return vectors[0]


def _validate_token_limit(base_url: str, text: str, context_size: int, *, connection: Any = None) -> int:
    """Usa o tokenizer do próprio GGUF; não estima nem trunca registros."""
    from urllib.request import Request, urlopen

    body = json.dumps({"content": text, "add_special": True}).encode("utf-8")
    if connection is None:
        request = Request(base_url.removesuffix("/v1") + "/tokenize", data=body,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    else:
        connection.request("POST", "/tokenize", body=body, headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        payload = json.loads(response.read())
        if response.status != 200:
            raise RuntimeError(f"tokenizer retornou HTTP {response.status}: {payload}")
    count = len(payload["tokens"])
    if count >= context_size:
        raise ValueError(
            f"texto com {count} tokens excede o limite seguro de {context_size - 1}; "
            "prepare a segmentação/representação antes de gerar embeddings"
        )
    return count


def _stop_server(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _pipeline_arguments(args: argparse.Namespace, endpoint: str) -> list[str]:
    command = [
        "--input",
        str(args.input.resolve()),
        "--output",
        str(args.output.resolve()),
        "--batch-size",
        str(args.batch_size),
        "--pause-seg",
        str(args.pause_seconds),
        "--provider",
        "custom",
        "--endpoint",
        endpoint,
        "--model",
        args.model_name,
    ]
    if args.report:
        command.extend(["--report", str(args.report.resolve())])
    command.extend(["--encoding-config", json.dumps({
        "checkpoint": str(args.model_path.resolve()),
        "checkpoint_sha256": file_sha256(args.model_path),
        "backend": "llama.cpp", "pooling": "last", "context_size": args.context_size,
        "threads": args.threads, "document_instruction": None,
        "gpu_layers": args.gpu_layers, "parallel": args.parallel,
        "server_context_size": args.context_size * args.parallel,
        "ubatch_size": args.ubatch_size, "flash_attention": args.flash_attn,
    }, sort_keys=True)])
    return command


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--smoke-test",
        action="store_true",
        help="gera um vetor de teste em memória e não grava arquivos",
    )
    mode.add_argument(
        "--run",
        action="store_true",
        help="processa o JSONL e grava embeddings/cache/relatório",
    )
    mode.add_argument("--compatibility-test", action="store_true", help="integração explícita local/ECOA antes do corpus")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--llama-exe", default="llama.exe", help="executável llama.cpp")
    parser.add_argument("--host", default=DEFAULT_HOST, help="por segurança, use 127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--context-size", type=int, default=DEFAULT_CONTEXT_SIZE)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--gpu-layers", default="auto", help="auto, all ou número de camadas na GPU")
    parser.add_argument("--parallel", type=int, default=1, help="slots, cada um com context-size tokens")
    parser.add_argument("--ubatch-size", type=int, default=4096, help="limite físico de tokens por lote do servidor")
    parser.add_argument("--flash-attn", choices=("on", "off", "auto"), default="auto")
    parser.add_argument("--model-name", help="nome registrado nos embeddings e no cache")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, help="JSONL para retomada; padrão: pasta com data/hora e base")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--pause-seconds", type=float, default=0)
    args = parser.parse_args(argv)

    if args.host != DEFAULT_HOST:
        parser.error("o servidor local deve escutar somente em 127.0.0.1")
    if args.port < 1 or args.port > 65535:
        parser.error("--port deve estar entre 1 e 65535")
    if args.context_size < 1 or args.threads < 1 or args.batch_size < 1:
        parser.error("context-size, threads e batch-size devem ser positivos")
    if args.parallel < 1 or args.ubatch_size < 1:
        parser.error("parallel e ubatch-size devem ser positivos")
    if args.gpu_layers not in {"auto", "all"} and not args.gpu_layers.isdecimal():
        parser.error("gpu-layers deve ser auto, all ou um inteiro não negativo")
    if args.pause_seconds < 0:
        parser.error("--pause-seconds não pode ser negativo")
    if not args.model_path.is_file():
        parser.error(f"modelo GGUF não encontrado: {args.model_path}")
    if args.run and not args.input.is_file():
        parser.error(f"JSONL de entrada não encontrado: {args.input}")
    if args.output is None:
        if args.run:
            with args.input.open(encoding="utf-8") as handle:
                bases = {str(json.loads(line).get("base_id", "")) for line in handle if line.strip()}
            if len(bases) != 1 or not all(
                char.isalnum() or char in "_-" for char in next(iter(bases), "")
            ) or "" in bases:
                parser.error("use um JSONL de uma única base para organizar a saída automaticamente")
            stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S_%f")
            args.output = DEFAULT_OUTPUT.parent / stamp / bases.pop() / "embeddings.jsonl"
        else:
            args.output = DEFAULT_OUTPUT
    if args.output.resolve() == args.input.resolve():
        parser.error("--output não pode sobrescrever --input")

    if args.model_name is None:
        args.model_name = args.model_path.stem
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    executable = _resolve_executable(args.llama_exe)
    _check_port(args.port)

    base_url = f"http://{args.host}:{args.port}/v1"
    command = [
        executable,
        "serve",
        "--model",
        str(args.model_path.resolve()),
        "--embedding",
        "--pooling",
        "last",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--no-webui",
        "--ctx-size",
        str(args.context_size * args.parallel),
        "--threads",
        str(args.threads),
        "--threads-batch", str(args.threads),
        "--parallel", str(args.parallel),
        "--gpu-layers", args.gpu_layers,
        "--batch-size", str(args.ubatch_size),
        "--ubatch-size", str(args.ubatch_size),
        "--flash-attn", args.flash_attn,
    ]

    if "embedding" not in args.model_path.stem.lower():
        print(
            "Aviso: este GGUF não é um checkpoint dedicado de embeddings. "
            "O servidor pode retornar vetores, mas a qualidade semântica não é garantida.",
            file=sys.stderr,
        )

    with tempfile.TemporaryFile(mode="w+b") as server_log:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_for_server(process, base_url, server_log)
            if args.compatibility_test:
                return _check_compatibility(args, base_url)
            if args.smoke_test:
                vector = _request_embedding(base_url, args.model_name, SMOKE_TEST_TEXT)
                norm = math.sqrt(sum(value * value for value in vector))
                print(
                    json.dumps(
                        {
                            "status": "ok",
                            "model": args.model_name,
                            "dimensions": len(vector),
                            "norm": norm,
                            "files_written": False,
                        },
                        ensure_ascii=False,
                    )
                )
                return 0
            # Preflight completo antes da primeira escrita/chamada de embeddings.
            from contextlib import closing
            from http.client import HTTPConnection

            token_counts: dict[str, int] = {}
            validation = {"chunks": 0, "tokens": 0, "max_tokens": 0,
                          "limit_exclusive": min(args.context_size, args.ubatch_size),
                          "tokenizer": "llama.cpp/tokenize", "add_special": True}
            with closing(HTTPConnection(args.host, args.port, timeout=30)) as connection, args.input.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        record = json.loads(line)
                        try:
                            digest = _content_sha256(record["text"])
                            if digest not in token_counts:
                                token_counts[digest] = _validate_token_limit(
                                    base_url, record["text"], validation["limit_exclusive"], connection=connection,
                                )
                            tokens = token_counts[digest]
                            validation["chunks"] += 1
                            validation["tokens"] += tokens
                            validation["max_tokens"] = max(validation["max_tokens"], tokens)
                            if validation["chunks"] % 5000 == 0:
                                print(json.dumps({"preflight_progress": validation["chunks"]}), flush=True)
                        except ValueError as error:
                            raise ValueError(f"chunk {record['chunk_id']}: {error}") from error
            args.output.parent.mkdir(parents=True, exist_ok=True)
            validation["tokenizer_calls"] = len(token_counts)
            validation_path = args.output.with_name("token_validation.json")
            validation_path.write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
            print(json.dumps({"preflight": validation, "output": str(args.output)}, ensure_ascii=False), flush=True)
            return create_embeddings_main(_pipeline_arguments(args, base_url))
        finally:
            _stop_server(process)


if __name__ == "__main__":
    raise SystemExit(main())
