"""OCR das páginas de PDF sem camada de texto (atas escaneadas do PNCP).

Renderiza a página com ``pypdfium2`` (sem depender do Poppler) e reconhece o
texto com o Tesseract via ``pytesseract``. O executável do Tesseract e o modelo
de idioma (``por``) são instalados fora do pip; sem eles, ``disponivel()``
devolve ``False`` e a extração segue como antes, marcando a página ``sem_texto``.
"""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

import config

# Local do instalador UB-Mannheim (winget install UB-Mannheim.TesseractOCR).
_TESSERACT_WINDOWS = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")


def _tesseract_cmd() -> str | None:
    if config.TESSERACT_CMD:
        return config.TESSERACT_CMD if Path(config.TESSERACT_CMD).exists() else None
    return shutil.which("tesseract") or (str(_TESSERACT_WINDOWS) if _TESSERACT_WINDOWS.exists() else None)


@lru_cache(maxsize=1)
def disponivel() -> bool:
    """Indica se as bibliotecas, o executável e o idioma configurado existem."""
    try:
        import pypdfium2  # noqa: F401
        import pytesseract
    except ImportError:
        return False
    cmd = _tesseract_cmd()
    if cmd is None:
        return False
    pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        return config.OCR_LANG in pytesseract.get_languages(config="")
    except (pytesseract.TesseractError, OSError):
        return False


def motor() -> str:
    """Identifica o OCR usado, para compor a impressão digital do cache."""
    return f"tesseract:{config.OCR_LANG}:{config.OCR_DPI}" if disponivel() else "sem-ocr"


def texto_da_pagina(pdf_path: Path, page_index: int) -> str:
    """Reconhece o texto de uma página (índice a partir de 0)."""
    import pypdfium2 as pdfium
    import pytesseract

    documento = pdfium.PdfDocument(str(pdf_path))
    try:
        imagem = documento[page_index].render(scale=config.OCR_DPI / 72).to_pil()
    finally:
        documento.close()
    return pytesseract.image_to_string(imagem, lang=config.OCR_LANG).strip()
