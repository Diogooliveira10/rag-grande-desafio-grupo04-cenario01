from __future__ import annotations

import ssl
import sys

import certifi

from src.ssl_context import criar_contexto_ssl


def _raizes(contexto: ssl.SSLContext) -> set[tuple]:
    return {tuple(cert["subject"]) for cert in contexto.get_ca_certs()}


def test_contexto_mantem_verificacao_de_certificado_e_hostname():
    contexto = criar_contexto_ssl()

    assert contexto.verify_mode == ssl.CERT_REQUIRED
    assert contexto.check_hostname is True


def test_contexto_inclui_as_raizes_do_certifi():
    so_certifi = ssl.create_default_context(cafile=certifi.where())

    assert _raizes(so_certifi) <= _raizes(criar_contexto_ssl())


def test_sem_certifi_usa_so_os_certificados_do_sistema(monkeypatch):
    monkeypatch.setitem(sys.modules, "certifi", None)  # faz o import falhar

    contexto = criar_contexto_ssl()

    assert contexto.verify_mode == ssl.CERT_REQUIRED
