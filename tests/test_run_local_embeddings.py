import json

import pytest

from src import run_local_embeddings


class _FakeServer:
    def __init__(self):
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode


def test_compatibility_requires_cosine_and_ranking_overlap():
    documents = [[1.0, float(i)] for i in range(7)]
    accepted = run_local_embeddings.compare_query_vectors([[1.0, 0.0]], [[1.0, 0.0]], documents)
    assert accepted["compatible"] is True
    assert accepted["mean_top5_overlap"] == 1.0
    rejected = run_local_embeddings.compare_query_vectors([[1.0, 0.0]], [[0.0, 1.0]], documents)
    assert rejected["compatible"] is False


@pytest.mark.parametrize("remote", [[[1.0]], [[0.0, 0.0]], [[float("nan"), 1.0]]])
def test_compatibility_rejects_invalid_vectors(remote):
    with pytest.raises(ValueError, match="vetores incompatíveis"):
        run_local_embeddings.compare_query_vectors([[1.0, 0.0]], remote, [[1.0, 0.0]] * 5)


def _mock_server(monkeypatch):
    server = _FakeServer()
    monkeypatch.setattr(run_local_embeddings, "_resolve_executable", lambda _: "llama.exe")
    monkeypatch.setattr(run_local_embeddings, "_check_port", lambda _: None)
    monkeypatch.setattr(run_local_embeddings.subprocess, "Popen", lambda *args, **kwargs: server)
    monkeypatch.setattr(run_local_embeddings, "_wait_for_server", lambda *args: None)
    return server


def test_default_output_groups_one_base_under_timestamp(tmp_path):
    model_path = tmp_path / "model.gguf"
    model_path.touch()
    input_path = tmp_path / "documents.jsonl"
    input_path.write_text('{"base_id":"contratos_estruturado"}\n', encoding="utf-8")
    args = run_local_embeddings._parse_args(
        ["--run", "--model-path", str(model_path), "--input", str(input_path)]
    )
    assert args.output.name == "embeddings.jsonl"
    assert args.output.parent.name == "contratos_estruturado"
    run_local_embeddings.datetime.strptime(args.output.parent.parent.name, "%Y-%m-%d_%H-%M-%S_%f")
    assert not args.output.exists()


def test_default_output_rejects_mixed_bases(tmp_path):
    model_path = tmp_path / "model.gguf"
    model_path.touch()
    input_path = tmp_path / "documents.jsonl"
    input_path.write_text('{"base_id":"atas_conversacional"}\n{"base_id":"pca_agregado"}\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        run_local_embeddings._parse_args(
            ["--run", "--model-path", str(model_path), "--input", str(input_path)]
        )


def test_keepalive_tokenizer_checks_limit_and_http_status():
    class Connection:
        status = 200
        def request(self, *args, **kwargs):
            self.body = json.loads(kwargs["body"])
        def getresponse(self):
            return self
        def read(self):
            return json.dumps({"tokens": [1, 2, 3]}).encode()
    connection = Connection()
    assert run_local_embeddings._validate_token_limit("http://127.0.0.1/v1", "registro", 4, connection=connection) == 3
    assert connection.body == {"content": "registro", "add_special": True}
    with pytest.raises(ValueError, match="limite seguro"):
        run_local_embeddings._validate_token_limit("http://127.0.0.1/v1", "registro", 3, connection=connection)
    connection.status = 500
    with pytest.raises(RuntimeError, match="HTTP 500"):
        run_local_embeddings._validate_token_limit("http://127.0.0.1/v1", "registro", 4, connection=connection)


def test_request_embedding_uses_local_openai_compatible_client(monkeypatch):
    calls = []
    monkeypatch.setenv("CUSTOM_API_KEY", "local")

    def fake_embed(texts, *, base_url, api_key, model):
        calls.append((texts, base_url, api_key, model))
        return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr(run_local_embeddings, "gerar_embeddings", fake_embed)

    result = run_local_embeddings._request_embedding(
        "http://127.0.0.1:18321/v1", "local-model", "smoke test"
    )

    assert result == [0.1, 0.2, 0.3]
    assert calls == [
        (["smoke test"], "http://127.0.0.1:18321/v1", "local", "local-model")
    ]


def test_request_embedding_rejects_empty_vector(monkeypatch):
    monkeypatch.setattr(run_local_embeddings, "gerar_embeddings", lambda *args, **kwargs: [[]])

    with pytest.raises(RuntimeError, match="embedding vazio"):
        run_local_embeddings._request_embedding(
            "http://127.0.0.1:18321/v1", "local-model", "smoke test"
        )


@pytest.mark.parametrize("vector", [[float("nan")], [float("inf")], [0.0, 0.0]])
def test_request_embedding_rejects_invalid_vector(monkeypatch, vector):
    monkeypatch.setattr(run_local_embeddings, "gerar_embeddings", lambda *args, **kwargs: [vector])
    with pytest.raises(RuntimeError, match="norma nula"):
        run_local_embeddings._request_embedding("http://localhost/v1", "local", "texto")


def test_smoke_test_does_not_call_corpus_pipeline_or_write_output(tmp_path, monkeypatch, capsys):
    model_path = tmp_path / "model.gguf"
    model_path.touch()
    server = _mock_server(monkeypatch)
    monkeypatch.setattr(run_local_embeddings, "_request_embedding", lambda *args: [0.6, 0.8])
    monkeypatch.setattr(
        run_local_embeddings,
        "create_embeddings_main",
        lambda _: pytest.fail("smoke test should not process the corpus"),
    )

    result = run_local_embeddings.main(["--smoke-test", "--model-path", str(model_path)])

    output = json.loads(capsys.readouterr().out)
    assert result == 0
    assert output["status"] == "ok"
    assert output["dimensions"] == 2
    assert output["files_written"] is False
    assert server.terminated


def test_parallel_slots_keep_full_context_per_document(tmp_path, monkeypatch):
    model_path = tmp_path / "model.gguf"
    model_path.touch()
    server = _mock_server(monkeypatch)
    commands = []
    monkeypatch.setattr(run_local_embeddings.subprocess, "Popen", lambda command, **kwargs: commands.append(command) or server)
    monkeypatch.setattr(run_local_embeddings, "_request_embedding", lambda *args: [0.6, 0.8])
    run_local_embeddings.main(["--smoke-test", "--model-path", str(model_path), "--parallel", "4"])
    command = commands[0]
    assert command[command.index("--ctx-size") + 1] == "16384"
    assert command[command.index("--parallel") + 1] == "4"
    assert command[command.index("--ubatch-size") + 1] == "4096"
    assert server.terminated


def test_run_routes_to_existing_pipeline_and_stops_server(tmp_path, monkeypatch):
    model_path = tmp_path / "model.gguf"
    input_path = tmp_path / "documents.jsonl"
    output_path = tmp_path / "embeddings.jsonl"
    model_path.touch()
    input_path.touch()
    server = _mock_server(monkeypatch)
    captured = []
    monkeypatch.setattr(
        run_local_embeddings,
        "create_embeddings_main",
        lambda args: captured.extend(args) or 0,
    )

    result = run_local_embeddings.main(
        [
            "--run",
            "--model-path",
            str(model_path),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--batch-size",
            "3",
        ]
    )

    assert result == 0
    assert server.terminated
    assert "--provider" in captured
    assert captured[captured.index("--provider") + 1] == "custom"
    assert captured[captured.index("--endpoint") + 1] == "http://127.0.0.1:18321/v1"
    assert captured[captured.index("--model") + 1] == "model"
    assert captured[captured.index("--batch-size") + 1] == "3"
