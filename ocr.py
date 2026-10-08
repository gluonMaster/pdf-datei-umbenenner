"""Tesseract OCR adapter for the first page of scanned PDFs.

PyMuPDF is imported lazily, when the first page is rendered, so a missing or
broken renderer does not prevent the GUI from starting; processing itself
requires a working OCR setup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import csv
from dataclasses import dataclass, replace
from enum import Enum
import importlib
import io
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, Optional, Tuple

from page_data import ExtractionSource, PageData, PageImage, PageWord


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
    missing_languages: Tuple[str, ...] = ()


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
        message: str = "OCR-Erkennung erfolgreich abgeschlossen.",
    ) -> "OCRResult":
        return cls(OCRStatus.SUCCESS, page_data, message)

    @classmethod
    def unavailable(cls, message: str) -> "OCRResult":
        return cls(OCRStatus.UNAVAILABLE, None, message)

    @classmethod
    def failed(cls, message: str) -> "OCRResult":
        return cls(OCRStatus.FAILED, None, message)


class OCRProvider(ABC):
    """Boundary used by the PDF extractor and fake test providers."""

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
        rotation: int = 0,
    ) -> OCRResult:
        """Render, rotate clockwise and recognize the first page of ``pdf_path``."""

    def detect_rotation(self, image: PageImage) -> Optional[int]:
        """Clockwise correction of a rendered page, or ``None`` if unknown."""

        return None


def ocr_words_to_page_data(
    words: Iterable[OCRWord],
    *,
    image_width: float,
    image_height: float,
    page_width: float,
    page_height: float,
    text: Optional[str] = None,
    diagnostic: str = "OCR-Erkennung erfolgreich abgeschlossen.",
) -> PageData:
    """Scale image-pixel OCR boxes into top-origin PDF page coordinates."""

    if image_width <= 0 or image_height <= 0:
        raise ValueError("Die Abmessungen des OCR-Bildes müssen positiv sein.")
    if page_width <= 0 or page_height <= 0:
        raise ValueError("Die Abmessungen der PDF-Seite müssen positiv sein.")

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
    # Sparse-text mode keeps table cells such as the Fach column as separate
    # words; single-block mode (6) merged or dropped them on real forms.
    DEFAULT_PAGE_SEGMENTATION_MODE = 11
    QUERY_TIMEOUT_SECONDS = 20
    RECOGNITION_TIMEOUT_SECONDS = 120
    # OSD only orders the single retry; its result is verified by the form
    # structure, so a modest confidence is enough.
    MINIMUM_ORIENTATION_CONFIDENCE = 1.0

    def __init__(
        self,
        executable_path: Optional[os.PathLike[str] | str] = None,
        *,
        languages: Iterable[str] = DEFAULT_LANGUAGES,
        dpi: int = DEFAULT_DPI,
        page_segmentation_mode: Optional[int] = None,
    ) -> None:
        if dpi <= 0:
            raise ValueError("Die OCR-Auflösung (DPI) muss positiv sein.")
        requested_languages = tuple(
            dict.fromkeys(
                str(language).strip()
                for language in languages
                if str(language).strip()
            )
        )
        if not requested_languages:
            raise ValueError("Mindestens eine OCR-Sprache muss konfiguriert sein.")
        self.configured_executable = (
            Path(executable_path).expanduser() if executable_path else None
        )
        self.languages = requested_languages
        self.dpi = int(dpi)
        self.page_segmentation_mode = int(
            self.DEFAULT_PAGE_SEGMENTATION_MODE
            if page_segmentation_mode is None
            else page_segmentation_mode
        )
        # Only a successful check is reused: after installing Tesseract a new
        # check must see it, while a working engine is not queried per file.
        self._available: Optional[OCRAvailability] = None

    def check_availability(self, *, refresh: bool = False) -> OCRAvailability:
        if self._available is not None and not refresh:
            return self._available
        availability = self._query_availability()
        self._available = availability if availability.available else None
        return availability

    def _query_availability(self) -> OCRAvailability:
        executable = self._find_executable()
        if executable is None:
            return OCRAvailability(
                False,
                "OCR ist nicht verfügbar: Tesseract wurde weder in den Einstellungen "
                "noch über TESSERACT_CMD, PATH oder die Standard-Installationsordner "
                "gefunden.",
            )

        try:
            module = self._load_pymupdf()
        except ImportError:
            return OCRAvailability(
                False,
                "OCR ist nicht verfügbar: Der PyMuPDF-Renderer fehlt oder kann "
                "nicht geladen werden (start_app.bat repariert die Python-Pakete).",
                executable,
            )
        del module

        try:
            completed = self._run(
                [str(executable), "--list-langs"],
                timeout=self.QUERY_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return OCRAvailability(
                False,
                f"OCR ist nicht verfügbar: Tesseract konnte nicht abgefragt werden "
                f"({error}).",
                executable,
            )
        if completed.returncode != 0:
            detail = (
                self._decode(completed.stderr).strip() or "unbekannter Tesseract-Fehler"
            )
            return OCRAvailability(
                False,
                f"OCR ist nicht verfügbar: Die Abfrage der Sprachdaten ist "
                f"fehlgeschlagen ({detail}).",
                executable,
            )

        installed = self._parse_languages(self._decode(completed.stdout))
        missing = tuple(
            language for language in self.languages if language not in installed
        )
        if missing:
            return OCRAvailability(
                False,
                "OCR ist nicht verfügbar: Folgende Tesseract-Sprachdaten fehlen: "
                + ", ".join(missing)
                + ".",
                executable,
                installed,
                missing,
            )
        return OCRAvailability(
            True,
            "Tesseract und die konfigurierten Sprachdaten sind verfügbar.",
            executable,
            installed,
        )

    def recognize_first_page(
        self,
        pdf_path: Path,
        *,
        page_width: Optional[float] = None,
        page_height: Optional[float] = None,
        rotation: int = 0,
    ) -> OCRResult:
        """Render, optionally rotate clockwise, and recognize the first page.

        Coordinates use the rendered (upright) page, which already applies the
        PDF ``/Rotate`` entry; ``page_width``/``page_height`` are only a
        fallback for renderers that cannot report the page size.
        """

        availability = self.check_availability()
        if not availability.available or availability.executable is None:
            return OCRResult.unavailable(availability.message)

        try:
            image, rendered_width, rendered_height = self._render_first_page(
                Path(pdf_path), rotation
            )
            target_width = float(rendered_width or page_width or image.width)
            target_height = float(rendered_height or page_height or image.height)
            completed = self._run(
                [
                    str(availability.executable),
                    "stdin",
                    "stdout",
                    "-l",
                    "+".join(self.languages),
                    "--dpi",
                    str(image.dpi),
                    "--psm",
                    str(self.page_segmentation_mode),
                    "tsv",
                ],
                input_bytes=image.png,
            )
            if completed.returncode != 0:
                detail = (
                    self._decode(completed.stderr).strip()
                    or "unbekannter Tesseract-Fehler"
                )
                return OCRResult.failed(f"OCR fehlgeschlagen: {detail}")
            words = self._parse_tsv(self._decode(completed.stdout))
            if not words:
                return OCRResult.failed(
                    "OCR fehlgeschlagen: Tesseract hat keine Wörter erkannt."
                )
            page_data = ocr_words_to_page_data(
                words,
                image_width=image.width,
                image_height=image.height,
                page_width=target_width,
                page_height=target_height,
            )
            return OCRResult.success(
                replace(page_data, image=image, rotation=rotation % 360)
            )
        except Exception as error:
            return OCRResult.failed(
                f"OCR fehlgeschlagen: {str(error).strip() or error.__class__.__name__}"
            )

    def recognize_region(
        self,
        image: PageImage,
        box: Tuple[float, float, float, float],
    ) -> Tuple[OCRWord, ...]:
        """Recognize one text line inside a pixel box of a rendered page.

        Used as an additional reading of a single field. Coordinates of the
        returned words refer to the whole image. Failures return no words.
        """

        availability = self.check_availability()
        if not availability.available or availability.executable is None:
            return ()
        left = max(0, int(box[0]))
        top = max(0, int(box[1]))
        right = min(image.width, int(box[2]) + 1)
        bottom = min(image.height, int(box[3]) + 1)
        if right - left < 4 or bottom - top < 4:
            return ()
        try:
            from PIL import Image

            with Image.open(io.BytesIO(image.png)) as full:
                buffer = io.BytesIO()
                full.crop((left, top, right, bottom)).save(buffer, "PNG")
            completed = self._run(
                [
                    str(availability.executable),
                    "stdin",
                    "stdout",
                    "-l",
                    "+".join(self.languages),
                    "--dpi",
                    str(image.dpi),
                    "--psm",
                    "7",
                    "tsv",
                ],
                input_bytes=buffer.getvalue(),
            )
        except Exception:
            return ()
        if completed.returncode != 0:
            return ()
        return tuple(
            replace(word, left=word.left + left, top=word.top + top)
            for word in self._parse_tsv(self._decode(completed.stdout))
        )

    def detect_rotation(self, image: PageImage) -> Optional[int]:
        """Return the clockwise correction suggested by Tesseract OSD, if any.

        Tesseract reports ``Rotate`` counter-clockwise; it is converted to the
        clockwise convention of ``recognize_first_page``. ``None`` means that
        OSD data is missing or the result is unreliable.
        """

        availability = self.check_availability()
        if (
            not availability.available
            or availability.executable is None
            or "osd" not in availability.languages
        ):
            return None
        try:
            completed = self._run(
                [
                    str(availability.executable),
                    "stdin",
                    "stdout",
                    "--dpi",
                    str(image.dpi),
                    "--psm",
                    "0",
                ],
                input_bytes=image.png,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        values = {}
        for line in self._decode(completed.stdout).splitlines():
            key, separator, value = line.partition(":")
            if separator:
                values[key.strip().casefold()] = value.strip()
        try:
            rotate = (360 - int(values["rotate"])) % 360
            confidence = float(values["orientation confidence"])
        except (KeyError, ValueError):
            return None
        if rotate not in (0, 90, 180, 270):
            return None
        if confidence < self.MINIMUM_ORIENTATION_CONFIDENCE:
            return None
        return rotate

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

        for environment_name, subfolder in (
            ("ProgramFiles", ""),
            ("ProgramFiles(x86)", ""),
            # Per-user installations do not necessarily extend PATH.
            ("LOCALAPPDATA", "Programs"),
            ("LOCALAPPDATA", ""),
        ):
            root = os.environ.get(environment_name)
            if root:
                candidates.append(
                    Path(root) / subfolder / "Tesseract-OCR" / "tesseract.exe"
                )

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
        raise ImportError("PyMuPDF ist nicht installiert.")

    def _render_first_page(
        self,
        pdf_path: Path,
        rotation: int = 0,
    ) -> Tuple[PageImage, float, float]:
        """Render the visible first page, rotated clockwise by ``rotation``.

        Returns the image and the page size in points in the same orientation.
        """

        rotation %= 360
        if rotation not in (0, 90, 180, 270):
            raise ValueError("Drehung muss ein Vielfaches von 90 Grad sein.")
        pymupdf = self._load_pymupdf()
        document = pymupdf.open(str(pdf_path))
        try:
            if len(document) == 0:
                raise ValueError("PDF enthält keine Seiten.")
            page = document[0]
            scale = self.dpi / 72.0
            matrix = pymupdf.Matrix(scale, scale).prerotate(rotation)
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            width, height = float(page.rect.width), float(page.rect.height)
            if rotation in (90, 270):
                width, height = height, width
            return (
                PageImage(
                    pixmap.tobytes("png"),
                    int(pixmap.width),
                    int(pixmap.height),
                    self.dpi,
                    reader=self.recognize_region,
                ),
                width,
                height,
            )
        finally:
            document.close()

    @classmethod
    def _run(
        cls,
        command: list[str],
        *,
        input_bytes: Optional[bytes] = None,
        timeout: Optional[float] = None,
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
            timeout=cls.RECOGNITION_TIMEOUT_SECONDS if timeout is None else timeout,
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
        # Tesseract TSV has no quoting: a recognized word starting with '"'
        # must not open a quoted field that swallows the following rows.
        reader = csv.DictReader(
            io.StringIO(output),
            delimiter="\t",
            quoting=csv.QUOTE_NONE,
        )
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
