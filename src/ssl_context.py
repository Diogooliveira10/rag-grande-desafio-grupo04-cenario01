"""Contexto SSL compartilhado pelos downloads HTTPS (PNCP e artefatos).

No Windows o Python só enxerga as autoridades certificadoras que o sistema já
tem em cache; numa máquina onde a raiz de um site ainda não foi baixada, a
coleta falha com CERTIFICATE_VERIFY_FAILED. O contexto parte dos certificados
do sistema e acrescenta o bundle do ``certifi``, sem nunca desligar a
verificação. ``SSL_CERT_FILE`` continua valendo (lido pelo contexto padrão).
"""

from __future__ import annotations

import ssl


def criar_contexto_ssl() -> ssl.SSLContext:
    contexto = ssl.create_default_context()
    try:
        import certifi
    except ImportError:  # certifi ausente: fica só com os certificados do sistema
        return contexto
    contexto.load_verify_locations(cafile=certifi.where())
    return contexto


__all__ = ["criar_contexto_ssl"]
