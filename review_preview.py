"""Render scan fragments of the recognized fields for the review tab.

Runs in a worker thread and returns PNG bytes only; Tk images are created on
the main thread. Boxes use the upright page of the recognition, so the page is
rendered with the same rotation.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import io
from pathlib import Path
from typing import Dict, Optional, Tuple

FIELDS = (
    ("student_name", "Name"),
    ("birth_date", "Geburtsdatum"),
    ("subject", "Fach"),
)


@dataclass(frozen=True)
class PreviewImages:
    """Fragments as ``(label, png)``; a page thumbnail if no box is known."""

    fragments: Tuple[Tuple[str, bytes], ...] = ()
    page: Optional[bytes] = None
    error: Optional[str] = None


def _load_pymupdf():
    for name in ("pymupdf", "fitz"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise ImportError("PyMuPDF ist nicht installiert.")


def _png(image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def render_preview(
    pdf_path: Path,
    boxes: Dict[str, Tuple[float, float, float, float]],
    rotation: int = 0,
    *,
    dpi: int = 150,
    max_fragment_width: int = 520,
    thumbnail_width: int = 420,
) -> PreviewImages:
    """Crop the original scan around each known field box."""

    document = None
    try:
        pymupdf = _load_pymupdf()
        from PIL import Image

        # Read the file at once: an open handle would block moving the PDF
        # on Windows while the operator already applies a decision.
        document = pymupdf.open(stream=Path(pdf_path).read_bytes(), filetype="pdf")
        if len(document) == 0:
            return PreviewImages(error="Vorschau nicht verfügbar: PDF enthält keine Seiten.")
        page = document[0]
        rotation %= 360
        scale = dpi / 72.0
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(scale, scale).prerotate(rotation), alpha=False
        )
        image = Image.open(io.BytesIO(pixmap.tobytes("png")))
        width, height = float(page.rect.width), float(page.rect.height)
        if rotation in (90, 270):
            width, height = height, width
        scale_x, scale_y = image.width / width, image.height / height

        fragments = []
        for field, label in FIELDS:
            box = boxes.get(field)
            if not box:
                continue
            x0, top, x1, bottom = box
            if field == "subject":
                # The column box spans all rows; its first rows are enough.
                bottom = min(bottom, top + height * 0.045)
            pad_x, pad_y = width * 0.02, height * 0.008
            crop = image.crop((
                max(0, int((x0 - pad_x) * scale_x)),
                max(0, int((top - pad_y) * scale_y)),
                min(image.width, int((x1 + pad_x) * scale_x)),
                min(image.height, int((bottom + pad_y) * scale_y)),
            ))
            if crop.width < 2 or crop.height < 2:
                continue
            if crop.width > max_fragment_width:
                crop = crop.resize(
                    (max_fragment_width, max(1, crop.height * max_fragment_width // crop.width))
                )
            fragments.append((label, _png(crop)))

        page_png = None
        if not fragments:
            thumbnail = image.copy()
            thumbnail.thumbnail((thumbnail_width, int(thumbnail_width * 1.5)))
            page_png = _png(thumbnail)
        return PreviewImages(tuple(fragments), page_png)
    except Exception as error:
        detail = str(error).strip() or error.__class__.__name__
        return PreviewImages(error=f"Vorschau nicht verfügbar: {detail}")
    finally:
        if document is not None:
            document.close()


__all__ = ["FIELDS", "PreviewImages", "render_preview"]
