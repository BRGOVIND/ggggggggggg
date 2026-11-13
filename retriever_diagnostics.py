import json
from pathlib import Path

import pymupdf

from relay.evaluate import load_questions
from relay.papers import RetrievalSetting, build_indexes
from relay.run import PAPER_FOLDER

OUTPUT = Path("retriever_diagnostics.json")  # Save reproducible retrieval evidence.
QUESTIONS = Path("questions_candidate.json")  # Inspect frozen comparison wording.


def full_ranking(index, question: str) -> list:
    setting = index.setting
    index.setting = RetrievalSetting(
        setting.name, setting.words, len(index.passages), setting.overlap
    )
    try:
        return index.search(question)
    finally:
        index.setting = setting


def passage_record(passage, rank: int) -> dict:
    return {
        "rank": rank,
        "paper": passage.paper,
        "pdf_page": passage.page,
        "first_25_words": " ".join(passage.text.split()[:25]),
    }


def question_evidence(question: str, index, target_page: int | None) -> dict:
    ranked = full_ranking(index, question)
    evidence = {
        "corpus_chunks": len(index.passages),
        "nonzero_score_chunks": len(ranked),
        "top_chunks": [
            passage_record(passage, rank) for rank, passage in enumerate(ranked[:4], 1)
        ],
    }
    if target_page is not None:
        evidence["target_page_chunks"] = [
            passage_record(passage, rank)
            for rank, passage in enumerate(ranked, 1)
            if passage.paper == "Dynamic Chunking" and passage.page == target_page
        ]
    return evidence


def page_excerpt(path: Path, page_number: int, phrase: str, width: int = 160) -> str:
    with pymupdf.open(path) as paper:
        text = paper[page_number - 1].get_text(sort=True)
    position = text.lower().find(phrase.lower())
    if position < 0:
        return "not found"
    return text[max(0, position - 40) : position + width].replace("\n", " ")


def main() -> None:
    questions = {item["id"]: item for item in load_questions(QUESTIONS)}
    indexes = build_indexes(PAPER_FOLDER)
    evidence = {}
    for question_id in ("H02", "H03", "T03", "U01", "U02"):
        evidence[question_id] = {}
        for setting in ("small", "medium"):
            target = 3 if question_id == "H02" else None
            evidence[question_id][setting] = question_evidence(
                questions[question_id]["question"], indexes[setting], target
            )
    hnet = PAPER_FOLDER / "2507.07955v2.pdf"
    transformer = PAPER_FOLDER / "1706.03762v7.pdf"
    evidence["page_break"] = {
        "H03_pdf_page_6": page_excerpt(hnet, 6, "impedes gradient flow"),
        "H03_pdf_page_7": page_excerpt(hnet, 7, "exponential moving average"),
    }
    evidence["bleu_conflict"] = {
        "pdf_page": 8,
        "table_2": page_excerpt(transformer, 8, "41.8"),
        "section_6_1": page_excerpt(transformer, 8, "41.0"),
    }
    OUTPUT.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"Wrote {OUTPUT}")


if __name__ == "__main__":
    main()
