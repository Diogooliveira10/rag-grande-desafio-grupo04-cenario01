"""E: indexed/incremental traces and queued, cross-process coordinated writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import multiprocessing
from pathlib import Path
import threading

import pytest

from src import obs
from src.log_context import execution
from src.log_store import EventStore, INDEX_FILENAME, QueuedEventWriter
from src.trace import montar_trace


def payload(n, *, query="q_index", session="s_index", timestamp="2026-10-09T10:00:00+00:00"):
    return json.dumps({"ts": timestamp, "run_id": "r_index", "query_id": query, "session_id": session,
                      "stage": "geracao", "status": "ok", "latencia_ms": 1, "input": "pergunta",
                      "output": str(n), "tokens": {"prompt": 0, "completion": 0, "total": 0},
                      "modelo": None, "extra": {"sequencia": n}})


def test_warm_trace_uses_sql_index_without_reading_jsonl(tmp_path, monkeypatch):
    path = tmp_path / "pipeline-2026-10-09.jsonl"
    store = EventStore(tmp_path)
    store.append_batch([(path, payload(n)) for n in range(100)])
    assert len(store.records("query_id", "q_index")) == 100
    original = Path.open
    def forbid_jsonl(self, *args, **kwargs):
        if self.suffix == ".jsonl":
            pytest.fail("warm indexed trace must not read JSONL contents")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", forbid_jsonl)
    trace = montar_trace("q_index", logs_dir=tmp_path)
    assert len(trace["etapas"]) == 100
    with store.connect() as conn:
        plan = conn.execute("EXPLAIN QUERY PLAN SELECT payload FROM events WHERE query_id=? ORDER BY id", ("q_index",)).fetchall()
    assert "idx_events_query" in str(plan)


def test_legacy_file_import_is_incremental(tmp_path, monkeypatch):
    path = tmp_path / "pipeline-2026-10-09.jsonl"
    path.write_text(payload(1) + "\n", encoding="utf-8")
    store = EventStore(tmp_path)
    assert len(store.records("query_id", "q_index")) == 1
    offset = path.stat().st_size
    with path.open("a", encoding="utf-8") as stream:
        stream.write(payload(2) + "\n")
    seeks = []
    original = Path.open
    class Source:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *a): self.stream.close()
        def seek(self, value, *a): seeks.append(value); return self.stream.seek(value, *a)
        def __getattr__(self, key): return getattr(self.stream, key)
    def spy(self, *args, **kwargs):
        stream = original(self, *args, **kwargs)
        return Source(stream) if self == path and args and args[0] == "rb" else stream
    monkeypatch.setattr(Path, "open", spy)
    assert len(store.records("query_id", "q_index")) == 2
    assert seeks == [offset]


def test_replacement_truncation_and_deletion_refresh_the_derived_index(tmp_path):
    path = tmp_path / "pipeline-2026-10-09.jsonl"
    store = EventStore(tmp_path)
    store.append_batch([(path, payload(n)) for n in range(3)])
    path.write_text(payload(8) + "\n", encoding="utf-8")
    assert [r["output"] for r in store.records("query_id", "q_index")] == ["8"]
    replacement = tmp_path / "replacement"
    replacement.write_text(payload(9) + "\n", encoding="utf-8")
    replacement.replace(path)
    assert [r["output"] for r in store.records("query_id", "q_index")] == ["9"]
    path.unlink()
    assert store.records("query_id", "q_index") == []


def test_legacy_duplicates_remain_once_until_all_source_copies_are_deleted(tmp_path):
    directory = tmp_path / "pipeline"
    directory.mkdir()
    a = directory / "pipeline-2026-10-09.jsonl"
    b = tmp_path / a.name
    a.write_text(payload(1) + "\n", encoding="utf-8")
    b.write_text(payload(1) + "\n", encoding="utf-8")
    store = EventStore(directory)
    assert len(store.records("session_id", "s_index", [tmp_path])) == 1
    a.unlink()
    assert len(store.records("session_id", "s_index", [tmp_path])) == 1
    b.unlink()
    assert store.records("session_id", "s_index", [tmp_path]) == []


def test_partial_tail_is_not_indexed_until_completed_and_does_not_corrupt_next_event(tmp_path):
    path = tmp_path / "pipeline-2026-10-09.jsonl"
    path.write_text('{"broken":', encoding="utf-8")
    store = EventStore(tmp_path)
    assert store.records("query_id", "q_index") == []
    store.append_batch([(path, payload(1))])
    assert len(store.records("query_id", "q_index")) == 1
    assert json.loads(path.read_text().splitlines()[-1])["output"] == "1"


def test_queue_is_async_bounded_and_flushes_before_shutdown(tmp_path, monkeypatch):
    writer = QueuedEventWriter(tmp_path, queue_size=1)
    entered, release = threading.Event(), threading.Event()
    original = writer.store.append_batch
    def slow(events):
        entered.set()
        assert release.wait(5)
        original(events)
    monkeypatch.setattr(writer.store, "append_batch", slow)
    path = tmp_path / "pipeline-2026-10-09.jsonl"
    try:
        first = writer.submit(path, payload(1))
        assert entered.wait(5)
        assert not first.done.is_set()  # The producer already returned.
        writer.submit(path, payload(2))
        submitted = threading.Event()
        thread = threading.Thread(target=lambda: (writer.submit(path, payload(3)), submitted.set()))
        thread.start()
        assert not submitted.wait(.05)  # Full queue applies backpressure.
        release.set()
        thread.join(5)
        writer.flush()
        assert submitted.is_set()
    finally:
        release.set()
        writer.close()
    assert len(EventStore(tmp_path).records("query_id", "q_index")) == 3
    assert not writer.thread.is_alive()


def test_threaded_queries_capture_origin_session_and_sequence_before_enqueue(tmp_path):
    logger = obs.PipelineLogger(tmp_path)
    def run(i):
        with obs.sessao(f"s_{i}"), execution(f"r_{i}", f"q_{i}", origin="teste"):
            for n in range(20):
                logger.write(obs.LogRecord(ts=datetime.now().astimezone().isoformat(), run_id=f"r_{i}",
                    query_id=f"q_{i}", stage=obs.Stage.GERACAO, status="ok", latencia_ms=1, input=str(n), output=str(i)))
            logger.write(obs.LogRecord(ts=datetime.now().astimezone().isoformat(), run_id=f"r_{i}",
                query_id=f"q_{i}", stage=obs.Stage.PIPELINE_FIM, status="ok", latencia_ms=1, input="fim", output="fim"))
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run, range(4)))
        store = EventStore(tmp_path)
        for i in range(4):
            events = store.records("query_id", f"q_{i}")
            assert len(events) == 21
            assert {e["session_id"] for e in events} == {f"s_{i}"}
            assert {e["extra"]["origem"] for e in events} == {"teste"}
            assert [e["extra"]["sequencia"] for e in events] == list(range(1, 22))
    finally:
        logger.close()


def process_write(directory, worker):
    # Spawn-safe helper; every process owns a queue but SQLite owns the write lock.
    writer = QueuedEventWriter(directory)
    path = Path(directory) / "pipeline-2026-10-09.jsonl"
    try:
        for i in range(30):
            writer.submit(path, payload(i, query=f"q_process_{worker}", session=f"s_process_{worker}"))
    finally:
        writer.close()
    return worker


def test_multiple_processes_coordinate_jsonl_append_and_index(tmp_path):
    context = multiprocessing.get_context("spawn")
    with context.Pool(3) as pool:
        assert pool.starmap(process_write, [(str(tmp_path), i) for i in range(3)]) == [0, 1, 2]
    lines = (tmp_path / "pipeline-2026-10-09.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 90 and all(isinstance(json.loads(line), dict) for line in lines)
    store = EventStore(tmp_path)
    for i in range(3):
        assert len(store.records("query_id", f"q_process_{i}")) == 30


def test_derived_index_can_be_rebuilt_from_jsonl(tmp_path):
    store = EventStore(tmp_path)
    path = tmp_path / "pipeline-2026-10-09.jsonl"
    store.append_batch([(path, payload(1))])
    store.index_path.unlink()
    assert len(EventStore(tmp_path).records("query_id", "q_index")) == 1


def test_invalid_lookup_field_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        EventStore(tmp_path).records("query_id; DROP TABLE events", "q_index")


def test_queue_reports_persistence_failure_and_keeps_consuming(tmp_path, monkeypatch, caplog):
    writer = QueuedEventWriter(tmp_path)
    original = writer.store.append_batch
    errors = []
    def broken(events):
        raise OSError("disco indisponível")
    monkeypatch.setattr(writer.store, "append_batch", broken)
    path = tmp_path / "pipeline-2026-10-09.jsonl"
    try:
        failed = writer.submit(path, payload(1), errors.append)
        writer.flush()
        assert failed.done.is_set() and isinstance(failed.error, OSError)
        assert errors == [failed.error]
        assert "Falha ao persistir lote de logs" in caplog.text
        monkeypatch.setattr(writer.store, "append_batch", original)
        success = writer.submit(path, payload(2))
        writer.flush()
        assert success.error is None
        assert [r["output"] for r in writer.store.records("query_id", "q_index")] == ["2"]
    finally:
        writer.close()
