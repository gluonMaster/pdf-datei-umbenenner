"""Structured data extracted from the first page of a document."""

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Callable, Iterable, Optional, Tuple


class ExtractionSource(str, Enum):
    """Origin of the text and coordinates stored in :class:`PageData`."""

    PDF_TEXT = "pdf_text"
    OCR = "ocr"


@dataclass(frozen=True)
class PageWord:
    """A word and its bounding box in PDF page coordinates."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    confidence: Optional[float] = None

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2


@dataclass(frozen=True)
class PageCharacter:
    """A character and its bounding box in PDF page coordinates."""

    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class PageImage:
    """Rendered page image, kept only while one document is processed.

    ``reader`` recognizes a pixel box of this image and returns words in image
    pixel coordinates; it is supplied by the OCR engine that rendered the page.
    """

    png: bytes = field(repr=False)
    width: int
    height: int
    dpi: int
    reader: Optional[Callable[["PageImage", Tuple[float, float, float, float]], tuple]] = field(
        default=None, repr=False, compare=False
    )


@dataclass(frozen=True)
class PageData:
    """Full text and positioned elements from one page.

    ``alternatives`` holds further independent readings of the same page, for
    example the embedded text layer next to an OCR reading. ``rotation`` is the
    clockwise angle applied to the rendered page so that its content is
    upright; all coordinates refer to that upright page.
    """

    width: float
    height: float
    text: str
    words: Tuple[PageWord, ...] = field(default_factory=tuple)
    characters: Tuple[PageCharacter, ...] = field(default_factory=tuple)
    source: ExtractionSource = ExtractionSource.PDF_TEXT
    diagnostics: Tuple[str, ...] = field(default_factory=tuple)
    ocr_status: Optional[str] = None
    alternatives: Tuple["PageData", ...] = field(default_factory=tuple)
    rotation: int = 0
    image: Optional[PageImage] = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Accept lists from adapters and synthetic tests while exposing immutable data.
        object.__setattr__(self, "words", tuple(self.words))
        object.__setattr__(self, "characters", tuple(self.characters))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))
        object.__setattr__(self, "alternatives", tuple(self.alternatives))

    @property
    def readings(self) -> Tuple["PageData", ...]:
        """This reading followed by its alternatives."""

        return (self, *self.alternatives)

    def without_image(self) -> "PageData":
        """Drop rendered images so results do not keep page bitmaps alive."""

        if self.image is None and all(item.image is None for item in self.alternatives):
            return self
        return replace(
            self,
            image=None,
            alternatives=tuple(item.without_image() for item in self.alternatives),
        )

    @property
    def chars(self) -> Tuple[PageCharacter, ...]:
        """Compatibility alias matching pdfplumber terminology."""

        return self.characters

    def words_in_region(
        self,
        x0: float,
        top: float,
        x1: float,
        bottom: float,
    ) -> Tuple[PageWord, ...]:
        """Return words whose centers are inside a rectangular page region."""

        return tuple(
            word
            for word in self.words
            if x0 <= word.center_x <= x1 and top <= word.center_y <= bottom
        )


def page_words(items: Iterable[PageWord]) -> Tuple[PageWord, ...]:
    """Small convenience helper for extraction adapters."""

    return tuple(items)
