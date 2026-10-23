"""Coordinated JSONL writes and a rebuildable, indexed SQLite/WAL trace cache.

The JSONL files stay authoritative. SQLite serializes writers across processes;
append and index updates share its write lock. Readers import only new bytes
from legacy/external JSONL files. No external service or new dependency.
"""
from __future__ import annotations

import atexit
from contextlib import closing
from dataclasses import dataclass, field
import hashlib
import json
import logging
import os
from pathlib import Path
from queue import Empty, Queue
import sqlite3
import threading
import time
from typing import Any, Callable, Iterable
import weakref

import config
from src import log_chain

INDEX_FILENAME = "pipeline-index.sqlite"


def _encadear(path: Path, payloads: list[str]) -> list[str]:
    """Acrescenta ``hash_anterior`` e ``hash`` a cada linha (src/log_chain.py)."""
    anterior = log_chain.ultimo_hash(path)
    encadeadas = []
    for payload in payloads:
        try:
            linha, anterior = log_chain.encadear(payload, anterior, config.LOG_CHAIN_KEY)
        except (ValueError, TypeError, AttributeError):
            # Payload que não é um objeto JSON segue como veio; o verificador o aponta.
            linha = payload
        encadeadas.append(linha)
    return encadeadas
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, event_hash TEXT NOT NULL UNIQUE,
    query_id TEXT, session_id TEXT, run_id TEXT, ts TEXT, payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_query ON events(query_id, id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE TABLE IF NOT EXISTS sources (
    path TEXT PRIMARY KEY, offset INTEGER NOT NULL, size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS event_sources (
    path TEXT NOT NULL, event_hash TEXT NOT NULL,
    PRIMARY KEY(path, event_hash)
);
CREATE INDEX IF NOT EXISTS idx_event_sources_hash ON event_sources(event_hash);
"""


class EventStore:
    def __init__(self, directory: Path | str):
        self.directory = Path(directory).resolve()
        self.index_path = self.directory / INDEX_FILENAME

    def connect(self) -> sqlite3.Connection:
        self.directory.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + 10
        while True:
            conn = sqlite3.connect(self.index_path, timeout=10, isolation_level=None)
            try:
                conn.execute("PRAGMA busy_timeout=10000")
                if conn.execute("PRAGMA journal_mode").fetchone()[0] != "wal":
                    conn.execute("PRAGMA journal_mode=WAL")
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version == 0:
                    conn.executescript(_SCHEMA)
                    conn.execute("PRAGMA user_version=1")
                elif version != 1:
                    raise sqlite3.DatabaseError("Versão do índice de logs não suportada")
                return conn
            except sqlite3.OperationalError as error:
                conn.close()
                # Initial WAL/schema creation can return SQLITE_BUSY without
                # honoring busy_timeout when several processes start together.
                code = getattr(error, "sqlite_errorcode", 0) & 0xff
                if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or time.monotonic() >= deadline:
                    raise
                time.sleep(.025)
            except BaseException:
                conn.close()
                raise

    @staticmethod
    def _insert(conn: sqlite3.Connection, path: str, payload: str) -> None:
        try:
            record = json.loads(payload)
        except (ValueError, UnicodeError):
            return  # Same tolerance as the legacy trace reader.
        if not isinstance(record, dict):
            return
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        conn.execute("INSERT OR IGNORE INTO events(event_hash,query_id,session_id,run_id,ts,payload) "
                     "VALUES(?,?,?,?,?,?)", (digest, record.get("query_id"), record.get("session_id"),
                     record.get("run_id"), record.get("ts"), payload))
        conn.execute("INSERT OR IGNORE INTO event_sources(path,event_hash) VALUES(?,?)", (path, digest))

    @staticmethod
    def _orphans(conn: sqlite3.Connection) -> None:
        conn.execute("DELETE FROM events WHERE NOT EXISTS "
                     "(SELECT 1 FROM event_sources s WHERE s.event_hash=events.event_hash)")

    @staticmethod
    def _signature(stat: os.stat_result) -> tuple[int, int, int, int]:
        """size, mtime_ns, device and inode as SQLite-safe signed 64-bit ints.

        On Windows ``st_dev`` is the unsigned volume serial and can exceed
        2**63 - 1, which SQLite rejects (OverflowError, losing the log batch).
        """
        def signed(value: int) -> int:
            return value - 2**64 if value >= 2**63 else value
        return stat.st_size, stat.st_mtime_ns, signed(stat.st_dev), signed(stat.st_ino)

    def _sync_file(self, conn: sqlite3.Connection, path: Path) -> None:
        if not path.is_file() or path.is_symlink():
            return
        name = str(path.resolve())
        size, mtime_ns, device, inode = self._signature(path.stat())
        old = conn.execute("SELECT offset,size,mtime_ns,device,inode FROM sources WHERE path=?", (name,)).fetchone()
        if old and (old[1], old[2], old[3], old[4]) == (size, mtime_ns, device, inode):
            return
        reset = bool(old and (size < old[0] or (old[3], old[4]) != (device, inode)
                              or (size == old[1] and mtime_ns != old[2])))
        offset = old[0] if old and not reset else 0
        if reset:
            conn.execute("DELETE FROM event_sources WHERE path=?", (name,))
        with path.open("rb") as source:
            source.seek(offset)
            while True:
                line = source.readline()
                if not line or not line.endswith(b"\n"):
                    break  # A writer may still be completing the last line.
                offset = source.tell()
                try:
                    self._insert(conn, name, line.decode("utf-8-sig"))
                except UnicodeError:
                    continue
        conn.execute("INSERT OR REPLACE INTO sources VALUES(?,?,?,?,?,?)",
                     (name, offset, size, mtime_ns, device, inode))
        if reset:
            self._orphans(conn)

    def append_batch(self, events: Iterable[tuple[Path, str]]) -> None:
        """WAL write transaction also coordinates JSONL append between processes.

        If a process dies after append but before commit, the next import repairs
        the index from JSONL. A trailing incomplete line is separated, not edited.
        """
        with closing(self.connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                grouped: dict[Path, list[str]] = {}
                for path, payload in events:
                    grouped.setdefault(path.resolve(), []).append(payload)
                for path, payloads in grouped.items():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    self._sync_file(conn, path)
                    if config.LOG_CHAIN_ENABLED:
                        # Dentro da transação: outro processo não grava entre a
                        # leitura do último hash e o acréscimo destas linhas.
                        payloads = _encadear(path, payloads)
                    prefix = b""
                    if path.exists() and path.stat().st_size:
                        with path.open("rb") as source:
                            source.seek(-1, 2)
                            if source.read(1) != b"\n":
                                prefix = b"\n"
                    encoded = prefix + "".join(payload + "\n" for payload in payloads).encode("utf-8")
                    with path.open("ab") as target:
                        target.write(encoded)
                    # Index the appended bytes once, including any legacy tail
                    # that the separator completed. JSONL remains authoritative.
                    self._sync_file(conn, path)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise

    def records(self, field_name: str, value: str, legacy_directories: Iterable[Path] = ()) -> list[dict[str, Any]]:
        if field_name not in {"query_id", "session_id", "run_id"}:
            raise ValueError("Campo de consulta de log não permitido")
        directories = {self.directory, *(Path(p).resolve() for p in legacy_directories)}
        paths = {path.resolve() for directory in directories for path in directory.glob("pipeline-*.jsonl")
                 if path.is_file() and not path.is_symlink()}
        with closing(self.connect()) as conn:
            known = {row[0]: row[1:] for row in conn.execute("SELECT * FROM sources")}
            changed = []
            for path in paths:
                old = known.get(str(path))
                if not old or (old[1], old[2], old[3], old[4]) != self._signature(path.stat()):
                    changed.append(path)
            removed = [name for name in known if Path(name).parent in directories and Path(name) not in paths]
            if changed or removed:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for path in sorted(changed):
                        self._sync_file(conn, path)
                    # The derived cache follows, but does not change, retention.
                    for name in removed:
                        conn.execute("DELETE FROM event_sources WHERE path=?", (name,))
                        conn.execute("DELETE FROM sources WHERE path=?", (name,))
                    if removed:
                        self._orphans(conn)
                    conn.commit()
                except BaseException:
                    conn.rollback()
                    raise
            rows = conn.execute(f"SELECT payload FROM events WHERE {field_name}=? ORDER BY id", (value,)).fetchall()
        return [json.loads(row[0]) for row in rows]


@dataclass
class _Job:
    path: Path | None = None
    payload: str | None = None
    on_error: Callable[[BaseException], None] | None = None
    done: threading.Event = field(default_factory=threading.Event)
    error: BaseException | None = None


_writers: weakref.WeakSet = weakref.WeakSet()
_STOP = object()


class QueuedEventWriter:
    """Bounded FIFO, batch persistence, explicit flush and orderly shutdown."""
    def __init__(self, directory: Path | str, queue_size: int = 256):
        self.store = EventStore(directory)
        self.queue: Queue = Queue(maxsize=queue_size)
        self._closed = False
        self._pid = os.getpid()
        self._submit_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name="pipeline-log-writer", daemon=True)
        _writers.add(self)
        self.thread.start()

    def submit(self, path: Path, payload: str, on_error: Callable[[BaseException], None] | None = None) -> _Job:
        if self._pid != os.getpid():
            raise OSError("Fila de logs herdada; crie um escritor no processo atual")
        job = _Job(path=path, payload=payload, on_error=on_error)
        with self._submit_lock:
            if self._closed:
                raise OSError("Escritor de logs já encerrado")
            self.queue.put(job)  # Backpressure, not silent event dropping.
        return job

    def flush(self) -> None:
        if self._pid != os.getpid():
            return
        barrier = _Job()
        with self._submit_lock:
            if self._closed:
                return
            self.queue.put(barrier)
        barrier.done.wait()

    def close(self) -> None:
        if self._pid != os.getpid():
            return  # Inherited queues have no consumer after fork.
        with self._submit_lock:
            if self._closed:
                return
            self._closed = True
            self.queue.put(_STOP)
        self.thread.join()

    def _run(self) -> None:
        while True:
            first = self.queue.get()
            if first is _STOP:
                self.queue.task_done()
                return
            batch = [first]
            stopping = False
            while first.payload is not None and len(batch) < 64:
                try:
                    job = self.queue.get_nowait()
                except Empty:
                    break
                if job is _STOP:
                    stopping = True
                    break
                batch.append(job)
                if job.payload is None:
                    break  # Flush barrier includes all previous jobs.
            events = [(job.path, job.payload) for job in batch if job.payload is not None]
            try:
                if events:
                    self.store.append_batch(events)
            except Exception as error:
                logging.getLogger(__name__).exception("Falha ao persistir lote de logs")
                for job in batch:
                    if job.payload is not None:
                        job.error = error
                        if job.on_error:
                            try:
                                job.on_error(error)
                            except Exception:
                                logging.getLogger(__name__).exception("Falha ao registrar erro de escrita")
            finally:
                for job in batch:
                    job.done.set()
                    self.queue.task_done()
            if stopping:
                self.queue.task_done()
                return


def _shutdown() -> None:
    for writer in list(_writers):
        writer.close()


atexit.register(_shutdown)
