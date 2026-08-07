"""Optional OCR adapter for image-only PDF first pages.

The module deliberately imports no optional package at import time.  PyMuPDF is
loaded only after a text-poor page has selected the OCR fallback, so the normal
application remains usable without OCR dependencies or a Tesseract install.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import csv
from dataclasses import dataclass
from enum import Enum
import importlib
import io
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, Optional, Tuple

from page_data import ExtractionSource, PageData, PageWord


class OCRStatus(str, Enum):
    """Stable, user-facing state of one OCR attempt."""

    SUCCESS = "OCR success"
    UNAVAILABLE = "OCR unavailable"
    FAILED = "OCR failed"


@dataclass(frozen=True)
class OCRAvailability:
    available: bool
    message: str
    executable: Optional[Path] = None
    languages: Tuple[str, ...] = ()


@dataclass(frozen=True)
class OCRWord:
    """One Tesseract word in rendered-image pixel coordinates."""

    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float
    line_key: Tuple[int, int, int, int] = (1, 1, 1, 1)


@dataclass(frozen=True)
class OCRResult:
    status: OCRStatus
    page_data: Optional[PageData]
    message: str

    @classmethod
    def success(
        cls,
        page_data: PageData,
        message: str = "OCR fallback completed.",
    ) -> "OCRResult":
        return cls(OCRStatus.SUCCESS, page_data, message)

    @classmethod
    def unavailable(cls, message: str) -> "OCRResult":
        return cls(OCRStatus.UNAVAILABLE, None, message)

    @classmethod
    def failed(cls, message: str) -> "OCRResult":
        return cls(OCRStatus.FAILED, None, message)


class OCRProvider(ABC):
    """Boundary used by the PDF fallback and fake test providers."""

    @abstractmethod
    def check_availability(self) -> OCRAvailability:
        """Report whether the engine, renderer and language data are usable."""

    @abstractmethod
    def recognize_first_page(
        self,
        pdf_path: Path,
        *,
        page_width: Optional[float] = None,
        page_height: Optional[float] = None,
    ) -> OCRResult:
        """Render and recognize the first page of ``pdf_path``."""


def ocr_words_to_page_data(
    words: Iterable[OCRWord],
    *,
    image_width: float,
    image_height: float,
    page_width: float,
    page_height: float,
    text: Optional[str] = None,
    diagnostic: str = "OCR fallback completed.",
) -> PageData:
    """Scale image-pixel OCR boxes into top-origin PDF page coordinates."""

    if image_width <= 0 or image_height <= 0:
        raise ValueError("OCR image dimensions must be positive.")
    if page_width <= 0 or page_height <= 0:
        raise ValueError("PDF page dimensions must be positive.")

    items = tuple(words)
    scale_x = page_width / image_width
    scale_y = page_height / image_height
    page_words = tuple(
        PageWord(
            text=item.text,
            x0=item.left * scale_x,
            top=item.top * scale_y,
            x1=(item.left + item.width) * scale_x,
            bottom=(item.top + item.height) * scale_y,
            confidence=max(0.0, min(1.0, float(item.confidence))),
        )
        for item in items
        if item.text.strip()
    )
    reconstructed = text if text is not None else _text_from_words(items)
    return PageData(
        width=float(page_width),
        height=float(page_height),
        text=reconstructed,
        words=page_words,
        source=ExtractionSource.OCR,
        diagnostics=(diagnostic,),
        ocr_status=OCRStatus.SUCCESS.value,
    )


def _text_from_words(words: Iterable[OCRWord]) -> str:
    lines: dict[Tuple[int, int, int, int], list[str]] = {}
    order: list[Tuple[int, int, int, int]] = []
    for word in words:
        if not word.text.strip():
            continue
        if word.line_key not in lines:
            lines[word.line_key] = []
            order.append(word.line_key)
        lines[word.line_key].append(word.text.strip())
    return "\n".join(" ".join(lines[key]) for key in order)


class TesseractOCRProvider(OCRProvider):
    """Render with PyMuPDF and obtain positioned TSV data from Tesseract."""

    DEFAULT_DPI = 300
    DEFAULT_LANGUAGES = ("deu", "eng")

    def __init__(
        self,
        executable_path: Optional[os.PathLike[str] | str] = None,
        *,
        languages: Iterable[str] = DEFAULT_LANGUAGES,
        dpi: int = DEFAULT_DPI,
        page_segmentation_mode: int = 6,
    ) -> None:
        if dpi <= 0:
            raise ValueError("OCR DPI must be positive.")
        requested_languages = tuple(
            dict.fromkeys(
                str(language).strip()
                for language in languages
                if str(language).strip()
            )
        )
        if not requested_languages:
            raise ValueError("At least one OCR language must be configured.")
        self.configured_executable = (
            Path(executable_path).expanduser() if executable_path else None
        )
        self.languages = requested_languages
        self.dpi = int(dpi)
        self.page_segmentation_mode = int(page_segmentation_mode)

    def check_availability(self) -> OCRAvailability:
        executable = self._find_executable()
        if executable is None:
            return OCRAvailability(
                False,
                "OCR unavailable: Tesseract executable was not found in the setting, "
                "TESSERACT_CMD, PATH, or standard install locations.",
            )

        try:
            module = self._load_pymupdf()
        except ImportError:
            return OCRAvailability(
                False,
                "OCR unavailable: optional PyMuPDF renderer is not installed "
                "(install requirements-ocr.txt).",
                executable,
            )
        del module

        try:
            completed = self._run([str(executable), "--list-langs"])
        except (OSError, subprocess.SubprocessError) as error:
            return OCRAvailability(
                False,
                f"OCR unavailable: Tesseract could not be queried ({error}).",
                executable,
            )
        if completed.returncode != 0:
            detail = (
                self._decode(completed.stderr).strip() or "unknown Tesseract error"
            )
            return OCRAvailability(
                False,
                f"OCR unavailable: language data query failed ({detail}).",
                executable,
            )

        installed = self._parse_languages(self._decode(completed.stdout))
        missing = tuple(
            language for language in self.languages if language not in installed
        )
        if missing:
            return OCRAvailability(
                False,
                "OCR unavailable: missing Tesseract language data: "
                + ", ".join(missing)
                + ".",
                executable,
                installed,
            )
        return OCRAvailability(
            True,
            "Tesseract and configured language data are available.",
            executable,
            installed,
        )

    def recognize_first_page(
        self,
        pdf_path: Path,
        *,
        page_width: Optional[float] = None,
        page_height: Optional[float] = None,
    ) -> OCRResult:
        availability = self.check_availability()
        if not availability.available or availability.executable is None:
            return OCRResult.unavailable(availability.message)

        try:
            png_bytes, image_width, image_height, rendered_width, rendered_height = (
                self._render_first_page(Path(pdf_path))
            )
            target_width = float(page_width or rendered_width)
            target_height = float(page_height or rendered_height)
            command = [
                str(availability.executable),
                "stdin",
                "stdout",
                "-l",
                "+".join(self.languages),
                "--dpi",
                str(self.dpi),
                "--psm",
                str(self.page_segmentation_mode),
                "tsv",
            ]
            completed = self._run(command, input_bytes=png_bytes)
            if completed.returncode != 0:
                detail = (
                    self._decode(completed.stderr).strip()
                    or "unknown Tesseract error"
                )
                return OCRResult.failed(f"OCR failed: {detail}")
            words = self._parse_tsv(self._decode(completed.stdout))
            if not words:
                return OCRResult.failed("OCR failed: Tesseract returned no printed words.")
            page_data = ocr_words_to_page_data(
                words,
                image_width=image_width,
                image_height=image_height,
                page_width=target_width,
                page_height=target_height,
            )
            return OCRResult.success(page_data)
        except Exception as error:
            return OCRResult.failed(
                f"OCR failed: {str(error).strip() or error.__class__.__name__}"
            )

    def _find_executable(self) -> Optional[Path]:
        candidates: list[Path] = []
        if self.configured_executable is not None:
            candidates.append(self.configured_executable)
        environment_path = os.environ.get("TESSERACT_CMD")
        if environment_path:
            candidates.append(Path(environment_path).expanduser())
        path_match = shutil.which("tesseract")
        if path_match:
            candidates.append(Path(path_match))

        for environment_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            root = os.environ.get(environment_name)
            if root:
                candidates.append(Path(root) / "Tesseract-OCR" / "tesseract.exe")

        seen: set[str] = set()
        for candidate in candidates:
            normalized = os.path.normcase(os.path.abspath(candidate))
            if normalized in seen:
                continue
            seen.add(normalized)
            if candidate.is_file():
                return candidate.resolve()
        return None

    @staticmethod
    def _load_pymupdf():
        for module_name in ("pymupdf", "fitz"):
            try:
                return importlib.import_module(module_name)
            except ImportError:
                continue
        raise ImportError("PyMuPDF is not installed")

    def _render_first_page(
        self,
        pdf_path: Path,
    ) -> Tuple[bytes, int, int, float, float]:
        pymupdf = self._load_pymupdf()
        document = pymupdf.open(str(pdf_path))
        try:
            if len(document) == 0:
                raise ValueError("PDF contains no pages.")
            page = document[0]
            scale = self.dpi / 72.0
            matrix = pymupdf.Matrix(scale, scale)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            return (
                pixmap.tobytes("png"),
                int(pixmap.width),
                int(pixmap.height),
                float(page.rect.width),
                float(page.rect.height),
            )
        finally:
            document.close()

    @staticmethod
    def _run(
        command: list[str],
        *,
        input_bytes: Optional[bytes] = None,
    ) -> subprocess.CompletedProcess[bytes]:
        startupinfo = None
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        return subprocess.run(
            command,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            startupinfo=startupinfo,
            timeout=120,
        )

    @staticmethod
    def _decode(value: bytes) -> str:
        return value.decode("utf-8", errors="replace")

    @staticmethod
    def _parse_languages(output: str) -> Tuple[str, ...]:
        return tuple(
            line.strip()
            for line in output.splitlines()
            if line.strip()
            and not line.casefold().startswith("list of available languages")
        )

    @staticmethod
    def _parse_tsv(output: str) -> Tuple[OCRWord, ...]:
        reader = csv.DictReader(io.StringIO(output), delimiter="\t")
        words = []
        for row in reader:
            text = str(row.get("text", "") or "").strip()
            if not text or str(row.get("level", "")) != "5":
                continue
            try:
                confidence = float(row.get("conf", -1))
                if confidence < 0:
                    continue
                words.append(
                    OCRWord(
                        text=text,
                        left=float(row["left"]),
                        top=float(row["top"]),
                        width=float(row["width"]),
                        height=float(row["height"]),
                        confidence=confidence / 100.0,
                        line_key=(
                            int(row.get("page_num", 1)),
                            int(row.get("block_num", 1)),
                            int(row.get("par_num", 1)),
                            int(row.get("line_num", 1)),
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return tuple(words)


__all__ = [
    "OCRAvailability",
    "OCRProvider",
    "OCRResult",
    "OCRStatus",
    "OCRWord",
    "TesseractOCRProvider",
    "ocr_words_to_page_data",
]
