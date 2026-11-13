import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

import pymupdf

PAPER_TITLES = {  # These names appear in every user-facing citation.
    "1706.03762v7.pdf": "Attention Is All You Need",
    "2410.00037v1.pdf": "Moshi",
    "2507.07955v2.pdf": "Dynamic Chunking",
}
WORD_PATTERN = re.compile(
    r"[a-z0-9]+(?:[.-][a-z0-9]+)*"
)  # Match technical terms consistently.
QUERY_STOP_WORDS = {  # Common question words swamp rare technical terms in BM25.
    "a",
    "an",
    "and",
    "are",
    "at",
    "by",
    "did",
    "do",
    "does",
    "for",
    "from",
    "how",
    "in",
    "is",
    "its",
    "of",
    "on",
    "or",
    "the",
    "their",
    "to",
    "was",
    "were",
    "what",
    "when",
    "which",
    "why",
    "with",
}
PAPER_ALIASES = {  # Explicit names guide retrieval without excluding cross-paper evidence.
    "Attention Is All You Need": ("transformer", "attention is all you need"),
    "Moshi": ("moshi", "mimi", "helium", "inner monologue"),
    "Dynamic Chunking": ("h-net", "dynamic chunking"),
}
PAPER_BOOST = 2.0  # Explicitly named paper should outrank incidental mentions.


@dataclass(frozen=True)
class Passage:
    paper: str
    page: int
    text: str

    @property
    def citation(self) -> str:
        return f"{self.paper}, PDF p. {self.page}"


@dataclass(frozen=True)
class RetrievalSetting:
    name: str
    words: int
    pieces: int
    overlap: int


SETTINGS = {  # Vary context size and count for the required quality/cost comparison.
    "small": RetrievalSetting("small", 300, 3, 50),
    "medium": RetrievalSetting("medium", 450, 4, 75),
    "large": RetrievalSetting("large", 700, 4, 100),
}


def words_in(text: str) -> list[str]:
    return WORD_PATTERN.findall(text.lower())


def named_papers(question: str) -> set[str]:
    lower = question.lower()
    return {
        title
        for title, aliases in PAPER_ALIASES.items()
        if any(alias in lower for alias in aliases)
    }


def read_pages(folder: Path) -> list[tuple[str, int, str]]:
    pages = []
    for filename, title in PAPER_TITLES.items():
        with pymupdf.open(folder / filename) as paper:
            for page_number, page in enumerate(paper, start=1):
                text = page.get_text(sort=True)
                pages.append((title, page_number, text))
    return pages


def split_page(
    paper: str, page: int, text: str, setting: RetrievalSetting
) -> list[Passage]:
    words = text.split()
    step = setting.words - setting.overlap
    passages = []
    for start in range(0, len(words), step):
        part = words[start : start + setting.words]
        if not part or (len(part) < 30 and passages):
            break
        passages.append(Passage(paper, page, " ".join(part)))
        if start + setting.words >= len(words):
            break
    return passages


class PaperIndex:
    def __init__(self, pages: list[tuple[str, int, str]], setting: RetrievalSetting):
        self.setting = setting
        self.passages = [
            passage
            for paper, page, text in pages
            for passage in split_page(paper, page, text, setting)
        ]
        self.lengths = []
        self.postings = defaultdict(list)
        for number, passage in enumerate(self.passages):
            counts = Counter(words_in(passage.text))
            self.lengths.append(sum(counts.values()))
            for term, count in counts.items():
                self.postings[term].append((number, count))
        self.mean_length = sum(self.lengths) / max(1, len(self.lengths))

    def search(self, question: str) -> list[Passage]:
        scores = defaultdict(float)
        count = len(self.passages)
        query_terms = set(words_in(question)) - QUERY_STOP_WORDS
        for term in query_terms:
            matches = self.postings.get(term, [])
            idf = math.log(1 + (count - len(matches) + 0.5) / (len(matches) + 0.5))
            for number, frequency in matches:
                length = self.lengths[number]
                adjusted = frequency + 1.2 * (0.25 + 0.75 * length / self.mean_length)
                scores[number] += idf * frequency * 2.2 / adjusted
        hints = named_papers(question)
        for number in scores:
            if self.passages[number].paper in hints:
                scores[number] *= PAPER_BOOST
        ranked = sorted(scores, key=scores.get, reverse=True)
        return [self.passages[number] for number in ranked[: self.setting.pieces]]


def build_indexes(folder: Path) -> dict[str, PaperIndex]:
    pages = read_pages(folder)
    return {name: PaperIndex(pages, setting) for name, setting in SETTINGS.items()}
