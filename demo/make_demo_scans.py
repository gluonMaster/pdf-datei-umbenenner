"""Generate the fictional demo scans in demo/scans.

Every name, date and signature is invented. The two layouts follow the
structure the parser expects (see form_parser.py): the attendance form
("Anwesenheitsnachweis") and the older form whose header line holds
"Nachname Vorname Fach". Pages are rendered as images with slight skew,
blur and noise, like office scans without a usable text layer.

usage: python demo/make_demo_scans.py
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

DEMO_DIR = Path(__file__).resolve().parent
SCANS_DIR = DEMO_DIR / "scans"
DPI = 200
SCALE = DPI / 72  # page coordinates below are PDF points (A4: 595 x 842)
PAGE = (round(595 * SCALE), round(842 * SCALE))
FOOTER = "Beispielformular mit fiktiven Daten – Demonstration für PDF Datei Umbenenner"


def _font(names: tuple[str, ...], size: float) -> ImageFont.FreeTypeFont:
    for name in names:
        try:
            return ImageFont.truetype(name, round(size * SCALE))
        except OSError:
            continue
    return ImageFont.load_default(round(size * SCALE))


def regular(size):
    return _font(("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"), size)


def bold(size):
    return _font(("arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"), size)


def handwriting(size):
    return _font(("Inkfree.ttf", "segoepr.ttf", "segoesc.ttf", "DejaVuSans-Oblique.ttf"), size)


class Sheet:
    def __init__(self):
        self.image = Image.new("L", PAGE, 255)
        self.draw = ImageDraw.Draw(self.image)

    def text(self, x, y, value, font, fill=20):
        self.draw.text((x * SCALE, y * SCALE), value, font=font, fill=fill)

    def line(self, x0, y0, x1, y1, width=0.8, fill=40):
        self.draw.line((x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE), fill=fill,
                       width=max(1, round(width * SCALE)))

    def rect(self, x0, y0, x1, y1, width=0.8):
        self.draw.rectangle((x0 * SCALE, y0 * SCALE, x1 * SCALE, y1 * SCALE), outline=40,
                            width=max(1, round(width * SCALE)))


@dataclass
class Attendance:
    first_name: str
    last_name: str
    birth_date: str
    subject: str
    sessions: int = 9
    gr_ez_column: bool = True
    teacher: str = "Paul Lehrmann"
    skew: float = 0.4
    noise: int = 10


@dataclass
class Legacy:
    last_name: str
    first_name: str
    subject: str
    teacher: str = "Paul Lehrmann"
    skew: float = -0.3
    noise: int = 8
    upside_down: bool = False
    topics: list = field(default_factory=lambda: [
        "Wiederholung Grundlagen", "Übungsaufgaben", "Vorbereitung Test",
        "Hausaufgabenbetreuung", "Lernkontrolle",
    ])


def attendance_form(form: Attendance) -> Image.Image:
    sheet = Sheet()
    sheet.text(40, 42, "Anwesenheitsnachweis", bold(18))
    sheet.text(40, 68, "Lernförderung im Rahmen von Bildung und Teilhabe", regular(10))

    # Fill-in fields: the value is typed above the line, the label below it.
    sheet.text(42, 112, f"{form.last_name} {form.first_name}", regular(13))
    sheet.line(40, 132, 320, 132)
    sheet.text(40, 136, "Name des Schülers", regular(8))
    sheet.text(362, 112, form.birth_date, regular(13))
    sheet.line(360, 132, 555, 132)
    sheet.text(360, 136, "Geburtsdatum", regular(8))
    sheet.text(42, 172, "Lernförderung in Kleingruppe oder Einzelunterricht", regular(11))
    sheet.line(40, 190, 555, 190)
    sheet.text(40, 194, "Leistungsbereich", regular(8))

    if form.gr_ez_column:
        columns = [("Datum", 40, 92), ("Uhrzeit", 92, 160), ("Dauer", 160, 200),
                   ("GR/EZ", 200, 245), ("Fach", 245, 345), ("Ort", 345, 410),
                   ("Unterschrift", 410, 555)]
    else:
        columns = [("Datum", 40, 92), ("Uhrzeit", 92, 160), ("Dauer", 160, 205),
                   ("Fach", 205, 320), ("Ort", 320, 395), ("Unterschrift", 395, 555)]
    left = {name: x0 for name, x0, _x1 in columns}
    top, row_height, rows = 250, 22, 15
    bottom = top + row_height * (rows + 1)
    sheet.rect(40, top, 555, bottom)
    for _name, x0, _x1 in columns[1:]:
        sheet.line(x0, top, x0, bottom)
    for index in range(1, rows + 1):
        sheet.line(40, top + index * row_height, 555, top + index * row_height, width=0.5)
    for name, x0, _x1 in columns:
        sheet.text(x0 + 4, top + 5, name, bold(9))

    rng = random.Random(form.last_name)
    day = 2
    for index in range(rows):
        y = top + (index + 1) * row_height + 5
        # The subject is printed in every row, sessions are filled in by hand.
        sheet.text(left["Fach"] + 4, y, form.subject, regular(10))
        if index < form.sessions:
            sheet.text(left["Datum"] + 4, y, f"{day:02d}.09.", regular(10))
            sheet.text(left["Uhrzeit"] + 4, y, "15:00–16:30", regular(10))
            sheet.text(left["Dauer"] + 4, y, "90", regular(10))
            if form.gr_ez_column:
                sheet.text(left["GR/EZ"] + 10, y - 2, "EZ", handwriting(11), fill=60)
            sheet.text(left["Ort"] + 4, y, "Schule", regular(10))
            sheet.text(left["Unterschrift"] + 10 + rng.randint(0, 30), y - 3,
                       form.first_name[0] + ". " + form.last_name[:5], handwriting(10), fill=70)
            day += rng.choice((2, 3, 4))

    sheet.text(40, 640, "Name der Lehrkraft", regular(9))
    sheet.text(150, 638, form.teacher, regular(12))
    sheet.line(148, 655, 400, 655)
    sheet.line(40, 740, 260, 740)
    sheet.text(40, 744, "Datum, Unterschrift der Lehrkraft", regular(8))
    sheet.line(320, 740, 555, 740)
    sheet.text(320, 744, "Datum, Unterschrift Erziehungsberechtigte(r)", regular(8))
    sheet.text(90, 720, form.teacher.split()[-1], handwriting(14), fill=70)
    sheet.text(40, 812, FOOTER, regular(7), fill=110)
    return scanned(sheet.image, form.skew, form.noise)


def legacy_form(form: Legacy) -> Image.Image:
    sheet = Sheet()
    # The header line holds "Nachname Vorname Fach", as on the older form.
    sheet.text(40, 46, f"{form.last_name} {form.first_name} {form.subject}", bold(16))
    sheet.line(40, 70, 555, 70, width=1.2)
    for index, (label, value) in enumerate((
        ("Bewilligungszeitraum", "von 01.08.2026 bis 31.01.2027"),
        ("Monat", "September 2026"),
        ("Unterrichtsart", "Einzelunterricht"),
        ("Lehrer(in)", form.teacher),
    )):
        y = 88 + index * 24
        sheet.text(40, y, label, bold(11))
        sheet.text(190, y, value, regular(11))

    top, row_height = 205, 26
    columns = [("Datum", 40, 110), ("Uhrzeit", 110, 190), ("Inhalt", 190, 420),
               ("Unterschrift", 420, 555)]
    rows = 12
    bottom = top + row_height * (rows + 1)
    sheet.rect(40, top, 555, bottom)
    for _name, x0, _x1 in columns[1:]:
        sheet.line(x0, top, x0, bottom)
    for index in range(1, rows + 1):
        sheet.line(40, top + index * row_height, 555, top + index * row_height, width=0.5)
    for name, x0, _x1 in columns:
        sheet.text(x0 + 4, top + 7, name, bold(10))
    for index, topic in enumerate(form.topics):
        y = top + (index + 1) * row_height + 7
        sheet.text(44, y, f"{3 + 4 * index:02d}.09.2026", regular(10))
        sheet.text(114, y, "14:00–15:30", regular(10))
        sheet.text(194, y, topic, regular(10))
        sheet.text(430, y - 3, form.first_name, handwriting(11), fill=70)

    sheet.line(40, 700, 260, 700)
    sheet.text(40, 704, "Unterschrift Lehrer(in)", regular(8))
    sheet.text(60, 682, form.teacher.split()[-1], handwriting(14), fill=70)
    sheet.text(40, 812, FOOTER, regular(7), fill=110)
    image = scanned(sheet.image, form.skew, form.noise)
    return image.rotate(180) if form.upside_down else image


def scanned(image: Image.Image, skew: float, noise: int) -> Image.Image:
    """Scanner look: slight skew, softness, grain and a grey paper tone."""

    image = image.rotate(skew, resample=Image.BICUBIC, fillcolor=255)
    image = image.filter(ImageFilter.GaussianBlur(0.6))
    grain = Image.effect_noise(image.size, noise)
    image = Image.blend(image, grain, 0.08)
    return image.point(lambda value: min(255, int(value * 0.96) + 6))


DOCUMENTS = {
    "Scan_2026-09-30_0001.pdf": Attendance("Lena", "Beispiel", "14.03.2013", "Mathematik"),
    "Scan_2026-09-30_0002.pdf": Attendance("Max", "Muster", "02.09.2012", "Deutsch", sessions=7),
    "Scan_2026-09-30_0003.pdf": Attendance("Jonas", "Schneider", "21.06.2014", "Englisch",
                                           gr_ez_column=False, skew=-0.8, noise=18),
    "Scan_2026-09-30_0004.pdf": Legacy("Weber", "Mia", "Biologie"),
    "Scan_2026-09-30_0005.pdf": Legacy("Probe", "Paula", "Physik", upside_down=True),
    # Not in the demo registry: the review proposes a new student.
    "Scan_2026-09-30_0006.pdf": Attendance("Sophie", "Hoffmann", "05.11.2013", "Mathematik",
                                           sessions=6),
    # "Latein" is not in subjects_config.json: the review proposes a new subject.
    "Scan_2026-09-30_0007.pdf": Attendance("Elias", "Neumann", "17.01.2012", "Latein",
                                           sessions=8),
}


def main():
    SCANS_DIR.mkdir(exist_ok=True)
    for name, form in DOCUMENTS.items():
        image = attendance_form(form) if isinstance(form, Attendance) else legacy_form(form)
        target = SCANS_DIR / name
        image.save(target, "PDF", resolution=DPI, quality=70)
        print(f"{target.relative_to(DEMO_DIR.parent)}  {target.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
