from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import config

DB_PATH = config.ROOT_DIR / "data" / "app_state" / "historico.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS consultas (
    query_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    query TEXT NOT NULL,
    top_k INTEGER NOT NULL,
    resposta_json TEXT NOT NULL,
    confidence_level TEXT,
    is_refusal INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id TEXT NOT NULL REFERENCES consultas (query_id),
    ts TEXT NOT NULL,
    avaliacao TEXT NOT NULL,
    comentario TEXT
);
"""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConsultaNaoEncontrada(Exception):
    """``registrar_feedback`` recebeu um ``query_id`` sem consulta correspondente."""


@dataclass(frozen=True)
class HistoricoStore:
    """Persistência SQLite do histórico de consultas e do feedback.

    Cada método abre e fecha sua própria conexão — o volume de uma API de
    desafio não justifica reaproveitar conexão como em ``structured_db.Banco``,
    e assim cada chamada já vê o schema criado (``CREATE TABLE IF NOT EXISTS``).
    """

    db_path: Path = field(default=DB_PATH)

    def _conectar(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conexao = sqlite3.connect(self.db_path)
        conexao.row_factory = sqlite3.Row
        conexao.executescript(_SCHEMA)
        return conexao

    def registrar_consulta(
        self,
        *,
        query_id: str,
        run_id: str,
        query: str,
        top_k: int,
        resposta: dict[str, Any],
    ) -> None:
        """Grava a pergunta e a resposta do pipeline no histórico."""
        with closing(self._conectar()) as conexao, conexao:
            conexao.execute(
                "INSERT OR REPLACE INTO consultas "
                "(query_id, run_id, ts, query, top_k, resposta_json, confidence_level, is_refusal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    query_id,
                    run_id,
                    _agora(),
                    query,
                    top_k,
                    json.dumps(resposta, ensure_ascii=False),
                    resposta.get("confidence_level"),
                    int(bool(resposta.get("is_refusal"))),
                ),
            )

    def obter_consulta(self, query_id: str) -> Optional[dict[str, Any]]:
        """Devolve a consulta registrada, ou ``None`` se ``query_id`` é desconhecido."""
        with closing(self._conectar()) as conexao:
            linha = conexao.execute(
                "SELECT * FROM consultas WHERE query_id = ?", (query_id,)
            ).fetchone()
            return self._linha_para_consulta(conexao, linha) if linha else None

    def registrar_feedback(
        self, *, query_id: str, avaliacao: str, comentario: Optional[str] = None
    ) -> None:
        """Associa uma avaliação a uma consulta já registrada.

        Levanta ``ConsultaNaoEncontrada`` se ``query_id`` não existir no
        histórico — a API traduz isso em HTTP 404 em vez de aceitar feedback
        órfão.
        """
        with closing(self._conectar()) as conexao, conexao:
            existe = conexao.execute(
                "SELECT 1 FROM consultas WHERE query_id = ?", (query_id,)
            ).fetchone()
            if existe is None:
                raise ConsultaNaoEncontrada(query_id)
            conexao.execute(
                "INSERT INTO feedback (query_id, ts, avaliacao, comentario) VALUES (?, ?, ?, ?)",
                (query_id, _agora(), avaliacao, comentario),
            )

    def obter_historico(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """As ``limit`` consultas mais recentes, com o feedback recebido (se houver)."""
        with closing(self._conectar()) as conexao:
            # "ts" tem resolução de segundo: várias consultas no mesmo
            # segundo empatam, então o rowid (ordem de inserção) desempata.
            linhas = conexao.execute(
                "SELECT * FROM consultas ORDER BY ts DESC, rowid DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._linha_para_consulta(conexao, linha) for linha in linhas]

    def _linha_para_consulta(self, conexao: sqlite3.Connection, linha: sqlite3.Row) -> dict[str, Any]:
        feedbacks = conexao.execute(
            "SELECT avaliacao, comentario, ts FROM feedback WHERE query_id = ? ORDER BY ts",
            (linha["query_id"],),
        ).fetchall()
        return {
            "query_id": linha["query_id"],
            "run_id": linha["run_id"],
            "ts": linha["ts"],
            "query": linha["query"],
            "resposta": json.loads(linha["resposta_json"]),
            "feedback": [dict(f) for f in feedbacks],
        }


default_store = HistoricoStore()

__all__ = ["HistoricoStore", "ConsultaNaoEncontrada", "default_store", "DB_PATH"]
