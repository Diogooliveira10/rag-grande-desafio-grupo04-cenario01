"""Plugin opt-in: pytest -p tests.offline_guard ...

Bloqueia DNS e sockets externos (não só download de modelos). Permite loopback
para fixtures locais. Usar CLI normal, não pytest.main via stdin no Windows:
processos spawn precisam de um __main__ importável.
"""
import socket

_CONNECT = socket.socket.connect
_GETADDRINFO = socket.getaddrinfo
_LOCAIS = {"localhost", "127.0.0.1", "::1", None}


def _resolver(host, *args, **kwargs):
    if host not in _LOCAIS:
        raise OSError("TEST_OFFLINE: resolução externa bloqueada")
    return _GETADDRINFO(host, *args, **kwargs)


def _conectar(self, address):
    if isinstance(address, tuple) and address[0] not in _LOCAIS:
        raise OSError("TEST_OFFLINE: conexão externa bloqueada")
    return _CONNECT(self, address)


def pytest_sessionstart(session):
    socket.getaddrinfo = _resolver
    socket.socket.connect = _conectar


def pytest_sessionfinish(session, exitstatus):
    socket.getaddrinfo = _GETADDRINFO
    socket.socket.connect = _CONNECT
