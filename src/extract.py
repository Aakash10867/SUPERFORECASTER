"""
PDF -> clean pages of prose.

Two stages, deliberately separated:

1. TEXT EXTRACTION. `pdftotext` (poppler) handles multi-column newspaper
   layouts well and keeps articles in reading order. pypdf is the fallback if
   poppler is not installed.

2. STRUCTURAL FILTER. This drops the things that are obviously not journalism
   in ANY newspaper -- classified ads, trustee notices, stock tables, weather
   grids, phone-number-laden display ads. It works on the SHAPE of the text,
   not on section names, so it generalises to papers we have never seen.

   The filter is deliberately CONSERVATIVE. A wasted paragraph costs almost
   nothing; a dropped front-page story is a question you will never ask, and
   you would never know you missed it. When in doubt, it lets text through and
   leaves the judgement to the triage model.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Page:
    number: int
    text: str

    @property
    def word_count(self) -> int:
        return len(self.text.split())


@dataclass
class Paper:
    path: Path
    fingerprint: str
    pages: list[Page]
    paper_guess: str
    issue_date_guess: str
    date_note: str = ""


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def fingerprint(path: Path) -> str:
    """
    Content hash, not filename. Rename the file, upload it twice under
    different names -- it is still recognised as already read. A filename-based
    check would silently re-read the same paper and generate duplicate
    questions from stale news.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _pdftotext_available() -> bool:
    return shutil.which("pdftotext") is not None


def extract_pages(path: Path) -> list[Page]:
    if _pdftotext_available():
        pages = _extract_poppler(path)
        if pages:
            return pages
    return _extract_pypdf(path)


def _extract_poppler(path: Path) -> list[Page]:
    try:
        out = subprocess.run(
            ["pdftotext", str(path), "-"],
            capture_output=True,
            timeout=300,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    if out.returncode != 0:
        return []
    text = out.stdout.decode("utf-8", errors="replace")
    # \x0c is the form feed character poppler puts between pages
    chunks = text.split("\x0c")
    return [Page(i + 1, c) for i, c in enumerate(chunks) if c.strip()]


def _extract_pypdf(path: Path) -> list[Page]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return []
    try:
        reader = PdfReader(str(path))
    except Exception:
        return []
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            t = page.extract_text() or ""
        except Exception:
            t = ""
        if t.strip():
            pages.append(Page(i + 1, t))
    return pages


# ---------------------------------------------------------------------------
# Identifying the paper
# ---------------------------------------------------------------------------

_PAPER_SIGNATURES = [
    ("The Washington Post", ["washington post", "washingtonpost.com"]),
    ("The Wall Street Journal", ["wall street journal", "wsj.com", "dow jones"]),
    ("Mint", ["livemint.com", "mint primer", "think ahead. think growth"]),
    ("The Times of India", ["times of india", "timesofindia"]),
    ("Hindustan Times", ["hindustan times", "hindustantimes"]),
    ("The Hindu", ["thehindu.com"]),
    ("Business Standard", ["business-standard", "business standard"]),
    ("The Economic Times", ["economictimes", "economic times"]),
    ("Financial Times", ["ft.com", "financial times"]),
    ("The New York Times", ["nytimes.com", "new york times"]),
    ("The Guardian", ["theguardian.com"]),
    ("The Indian Express", ["indianexpress.com", "indian express"]),
]

_MONTHS = ("january february march april may june july august september "
           "october november december").split()


def _spaced(word: str) -> str:
    """
    Build a pattern matching a word even when letter-spaced.

    Newspaper mastheads are often typeset with tracking, and the text layer
    preserves it: the Washington Post extracts as "WEDNES DAY, AUGUS T 19 , 2026"
    and sometimes "AU GUST 1 9 , 2 0 2 6". A plain \bword\b pattern misses both.
    """
    return r"\s*".join(re.escape(c) for c in word)


def _squeeze(text: str) -> str:
    """
    All whitespace and separators removed, lowercased.

    Matching against the squeezed string makes detection immune to letter
    spacing entirely, which is far more reliable than trying to write a
    spacing-tolerant pattern for every field.
    """
    return re.sub(r"[\s,\.\u00b7\u2022]+", "", text).lower()


_MONTH_PAT = "|".join(_MONTHS)

# Applied to squeezed text, so no whitespace tolerance is needed here.
_DATE_RE = re.compile(r"(%s)(\d{1,2})(\d{4})" % _MONTH_PAT)
_DATE_RE_ALT = re.compile(r"(\d{1,2})(%s)(\d{4})" % _MONTH_PAT)


def identify_paper(pages: list[Page], filename: str = "") -> tuple[str, str]:
    """
    Best-effort guess at which paper this is and what date it carries.

    THE FILENAME IS CHECKED FIRST, AND FOR GOOD REASON.
    On live run 8 two papers were misdated even though both filenames carried
    the date plainly: "BS - Delhi - 22-08-2026.pdf" was read as 5 JUNE, because
    the page-content parser found a June date somewhere inside and took it, and
    "The Wall Street Journal Weekend - August 22, 2026.pdf" came back as "date
    unknown". A 5-June paper was then fed to the screen and to every inside
    view as though it were that day's reporting.

    A filename is chosen by a human who knows which issue it is; page content
    is full of dates belonging to other things. So the filename wins, and a
    disagreement is reported rather than silently resolved.
    """
    head_raw = " ".join(p.text for p in pages[:4])
    head = head_raw.lower()
    head_squeezed = _squeeze(head_raw)

    name = "Unknown"
    for label, needles in _PAPER_SIGNATURES:
        for n in needles:
            if n in head or _squeeze(n) in head_squeezed:
                name = label
                break
        if name != "Unknown":
            break

    from_pages = _find_date(head_squeezed)
    from_name = _find_date(_squeeze(filename)) if filename else ""

    if from_name and from_pages and from_name != from_pages:
        # Both present and disagreeing: trust the filename, but say so.
        return name, from_name, (
            f"filename says {from_name}, page content says {from_pages}; "
            "using the filename"
        )
    return name, (from_name or from_pages), ""


# Numeric dates, as they appear in filenames: 22-08-2026, 22_08_2026,
# 2026-08-22, and whatever separator a download tool happened to use.
_NUM_DMY = re.compile(r"(\d{1,2})\D(\d{1,2})\D(20\d{2})")
_NUM_YMD = re.compile(r"(20\d{2})\D(\d{1,2})\D(\d{1,2})")


def _find_date(squeezed: str) -> str:
    for regex, order in ((_DATE_RE, "mdy"), (_DATE_RE_ALT, "dmy")):
        for m in regex.finditer(squeezed):
            if order == "mdy":
                month_name, day_s, year_s = m.group(1), m.group(2), m.group(3)
            else:
                day_s, month_name, year_s = m.group(1), m.group(2), m.group(3)
            day, year = int(day_s), int(year_s)
            if not (1 <= day <= 31 and 1900 < year < 2200):
                continue
            month = _MONTHS.index(month_name) + 1
            return f"{year:04d}-{month:02d}-{day:02d}"

    m = _NUM_YMD.search(squeezed)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    m = _NUM_DMY.search(squeezed)
    if m:
        day, month, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        # Ambiguous between d-m-y and m-d-y; prefer d-m-y unless impossible.
        if month > 12 and day <= 12:
            day, month = month, day
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{year:04d}-{month:02d}-{day:02d}"
    return ""


def _normalise_date(raw: str) -> str:
    """Public helper: parse a loose date string into YYYY-MM-DD, or ''."""
    return _find_date(_squeeze(raw))


# ---------------------------------------------------------------------------
# Structural filter
# ---------------------------------------------------------------------------

# Phrases that mark a block as legal notice, classified ad or listing. These
# appear in essentially every newspaper in the English-speaking world.
_JUNK_PHRASES = (
    "substitute trustee", "trustee's sale", "trustees' sale", "notice of sale",
    "notice is hereby given", "deed of trust", "foreclosure", "public auction",
    "in the circuit court", "case no.", "civil no.", "defendant(s)",
    "plaintiff(s)", "order nisi", "legal notices", "classified",
    "request for proposal", "invitation for bid", "notice inviting",
    "all that fee simple", "purchaser's sole remedy", "deposit of $",
    "terms of sale", "no warranty of any kind", "condominium fees",
    "for sale by owner", "help wanted", "obituaries", "death notice",
    "in memoriam", "funeral home", "crossword", "sudoku", "horoscope",
    "comics", "solution to", "bridge column", "call now", "toll free",
    "restrictions apply", "see store for details", "offer expires",
    "financing available", "free estimate", "no obligation",
    "subscription", "to subscribe", "delivery issues", "advertisement",
    "recipe", "servings", "tablespoon", "teaspoon", "preheat the oven",
    "box score", "final score", "standings", "batting", "innings",
    "rebounds", "touchdown", "the associated press tally",
)

# Lines that are pure navigation, mastheads, page furniture.
_FURNITURE_RE = re.compile(
    r"^(?:[A-Z0-9\s\.\-\|/&']{0,60})$"
)

_URL_RE = re.compile(r"https?://|www\.\S+")
_PHONE_RE = re.compile(r"\b(?:\(\d{3}\)|\d{3}[-.])\s?\d{3}[-.]\d{4}\b|\b\d{10}\b")
_MONEY_RE = re.compile(r"[$₹£€]\s?[\d,]+")


def _nonprose_ratio(block: str) -> float:
    if not block:
        return 1.0
    non = sum(1 for c in block if c.isdigit() or c in ".,%$₹£€()[]/|-+*#")
    return non / max(len(block), 1)


def _looks_like_junk(block: str, settings: dict) -> bool:
    """
    Decide whether a block is NOT journalism.

    This filter is deliberately conservative. Its job is to remove what is
    obviously not an article -- legal notices, classified ads, stock tables,
    weather grids -- so we do not pay a model to read them. It is NOT its job
    to judge relevance; the triage model does that, and it is much better at it.

    A wasted paragraph costs a fraction of a cent. A dropped front-page story
    is a question you will never ask, and you would never know you missed it.
    So every rule here requires a STRONG signal, and anything ambiguous is let
    through.
    """
    low = block.lower()
    words = block.split()

    # 1. Explicit junk vocabulary. These phrases essentially never appear in
    #    news reporting but are ubiquitous in notices, listings and puzzles.
    for phrase in _JUNK_PHRASES:
        if phrase in low:
            return True

    # 2. Fragments too short to contain a story.
    if len(words) < settings["filtering"]["min_block_words"]:
        return True

    # 3. Tables and price lists: mostly digits and punctuation.
    if _nonprose_ratio(block) > settings["filtering"]["max_nonprose_ratio"]:
        return True

    # 4. Mastheads and display-ad headlines: near-entirely capitals.
    #    Bylines are capitalised too, so this only fires on short blocks that
    #    are overwhelmingly upper case.
    letters = [c for c in block if c.isalpha()]
    if letters:
        upper_share = sum(1 for c in letters if c.isupper()) / len(letters)
        if upper_share > 0.75 and len(words) < 60:
            return True

    # 5. Advertising. The distinguishing mark of an ad is CONTACT DETAILS --
    #    phone numbers and web addresses -- not money. Financial journalism is
    #    full of dollar figures and must never be caught here. An earlier
    #    version of this rule counted money amounts and threw away the
    #    Washington Post's lead story on the national debt.
    phones = len(_PHONE_RE.findall(block))
    urls = len(_URL_RE.findall(block))
    money = len(_MONEY_RE.findall(block))

    if phones >= 2 and len(words) < 300:
        return True
    if phones >= 1 and urls >= 1 and len(words) < 200:
        return True
    if urls >= 3 and len(words) < 200:
        return True
    if phones >= 1 and money >= 3 and len(words) < 200:
        return True

    # 6. Captions, headline stacks and index listings: no sentence structure.
    #    Kept deliberately tight so it cannot catch a real opening paragraph.
    sentences = block.count(".") + block.count("?") + block.count("!")
    if sentences == 0 and len(words) < 45:
        return True

    return False


def clean_page(page: Page, settings: dict) -> str:
    """Return the page text with junk blocks removed."""
    # Split on blank lines: this is how pdftotext separates layout blocks.
    raw_blocks = re.split(r"\n\s*\n", page.text)
    kept = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        if _looks_like_junk(block, settings):
            continue
        kept.append(block)
    return "\n\n".join(kept)


def load_paper(path: Path, settings: dict) -> Paper | None:
    pages = extract_pages(path)
    if not pages:
        return None
    name, date, date_note = identify_paper(pages, path.name)
    return Paper(
        path=path,
        fingerprint=fingerprint(path),
        pages=pages,
        paper_guess=name,
        issue_date_guess=date,
        date_note=date_note,
    )
