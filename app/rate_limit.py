"""Limite de requisições por cliente, com um balde de fichas em memória.

Cada cliente tem um balde com ``por_minuto`` fichas, que se recarrega aos
poucos (uma ficha a cada ``60 / por_minuto`` segundos). Cada requisição gasta
uma; com o balde vazio, a API responde 429 com ``Retry-After``. Sem dependência
externa: o backend roda num processo só, então o estado em memória basta.

Pelo túnel (ngrok), toda requisição chega de 127.0.0.1. O IP de quem chamou
vem no ``X-Forwarded-For``, mas só vale quando a conexão vem de um proxy
confiável: de fora, qualquer cliente escreveria o cabeçalho que quisesse.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

# O backend escuta em 127.0.0.1: só o agente do túnel, na mesma máquina, chega
# por aqui com X-Forwarded-For.
PROXIES_CONFIAVEIS = frozenset({"127.0.0.1", "::1"})
# Acima disso, os baldes cheios (clientes parados) são descartados.
_MAXIMO_DE_CLIENTES = 10_000


@dataclass
class _Balde:
    fichas: float
    atualizado_em: float


class LimiteDeRequisicoes:
    """Balde de fichas por cliente; ``por_minuto <= 0`` desliga o limite."""

    def __init__(self, por_minuto: int, relogio: Callable[[], float] = time.monotonic) -> None:
        self.por_minuto = por_minuto
        self._relogio = relogio
        self._baldes: dict[str, _Balde] = {}
        self._trava = threading.Lock()

    @property
    def ligado(self) -> bool:
        return self.por_minuto > 0

    def consumir(self, cliente: str) -> int:
        """Gasta uma ficha; devolve 0 se passou, ou os segundos até a próxima."""
        if not self.ligado:
            return 0
        recarga_por_segundo = self.por_minuto / 60
        with self._trava:
            agora = self._relogio()
            balde = self._baldes.get(cliente)
            if balde is None:
                if len(self._baldes) >= _MAXIMO_DE_CLIENTES:
                    self._descartar_cheios(agora, recarga_por_segundo)
                balde = self._baldes[cliente] = _Balde(float(self.por_minuto), agora)
            else:
                balde.fichas = min(self.por_minuto,
                                   balde.fichas + (agora - balde.atualizado_em) * recarga_por_segundo)
                balde.atualizado_em = agora
            if balde.fichas >= 1:
                balde.fichas -= 1
                return 0
            return max(1, math.ceil((1 - balde.fichas) / recarga_por_segundo))

    def limpar(self) -> None:
        with self._trava:
            self._baldes.clear()

    def _descartar_cheios(self, agora: float, recarga_por_segundo: float) -> None:
        for cliente, balde in list(self._baldes.items()):
            if balde.fichas + (agora - balde.atualizado_em) * recarga_por_segundo >= self.por_minuto:
                del self._baldes[cliente]


def cliente_real(host_da_conexao: Optional[str], x_forwarded_for: Optional[str]) -> str:
    """IP de quem chamou: o do ``X-Forwarded-For`` só atrás de proxy confiável.

    Usa a última entrada, que é a que o proxy acrescentou; as anteriores vieram
    do cliente e podem ser inventadas.
    """
    host = host_da_conexao or "desconhecido"
    if host in PROXIES_CONFIAVEIS and x_forwarded_for:
        encaminhado = x_forwarded_for.split(",")[-1].strip()
        if encaminhado:
            return encaminhado
    return host
