from __future__ import annotations

import pytest

from src.retry import retry_delay


@pytest.mark.parametrize("attempt,esperado", [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (10, 8.0)])
def test_backoff_exponencial_com_teto_de_8_segundos(attempt, esperado):
    assert retry_delay(attempt, None) == esperado


def test_retry_after_do_servidor_tem_prioridade():
    assert retry_delay(1, "5") == 5.0


@pytest.mark.parametrize("retry_after,esperado", [("120", 15.0), ("-3", 0.0)])
def test_retry_after_fica_entre_0_e_15_segundos(retry_after, esperado):
    assert retry_delay(1, retry_after) == esperado


def test_retry_after_invalido_cai_no_backoff():
    # Retry-After também pode vir como data HTTP; nesse caso vale o backoff.
    assert retry_delay(3, "Wed, 21 Oct 2026 07:28:00 GMT") == 4.0
