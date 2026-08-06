"""Structured data extracted from the first page of a document."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Tuple


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
class PageData:
    """Full text and positioned elements from one page."""

    width: float
    height: float
    text: str
    words: Tuple[PageWord, ...] = field(default_factory=tuple)
    characters: Tuple[PageCharacter, ...] = field(default_factory=tuple)
    source: ExtractionSource = ExtractionSource.PDF_TEXT

    def __post_init__(self) -> None:
        # Accept lists from adapters and synthetic tests while exposing immutable data.
        object.__setattr__(self, "words", tuple(self.words))
        object.__setattr__(self, "characters", tuple(self.characters))

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
