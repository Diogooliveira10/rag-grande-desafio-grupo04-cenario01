import pytest

from src.artifact_downloader import _extension


@pytest.mark.parametrize(
    ("url", "content_type", "body", "disposition", "esperado"),
    [
        # PCA do PNCP: caminho sem extensão, nome real no cabeçalho.
        (
            "https://pncp.gov.br/api/pncp/v1/orgaos/42498600000171/pca/2025/csv",
            "application/octet-stream",
            b"Unidade;UASG",
            'attachment; filename="42498600000171 - ESTADO DO RIO DE JANEIRO - 2025.csv"',
            ".csv",
        ),
        # SIGA-RJ: extensão em maiúsculas no caminho.
        ("https://www.compras.rj.gov.br/siga/imagens/LICITACOES.CSV", "application/octet-stream", b"x", "", ".csv"),
        ("https://x/relatorio", "text/csv; charset=utf-8", b"a;b", "", ".csv"),
        ("https://x/arquivo", "application/octet-stream", b"x", "attachment; filename*=UTF-8''ata.pdf", ".pdf"),
        # Arquivo da ata: sem extensão nem cabeçalho, detectado pelo conteúdo.
        ("https://x/atas/2/arquivos/1", "application/octet-stream", b"%PDF-1.7", "", ".pdf"),
        ("https://x/dados", "application/json", b"{}", "", ".json"),
        ("https://x/desconhecido", "application/octet-stream", b"??", "", ".bin"),
    ],
)
def test_extension_detecta_formato(url, content_type, body, disposition, esperado):
    assert _extension(url, content_type, body, disposition) == esperado
