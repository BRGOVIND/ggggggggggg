import csv
import json
import re
from pathlib import Path

from relay.papers import build_indexes
from relay.run import PAPER_FOLDER

RESULTS = Path("results.csv")  # Grade only saved real comparison answers.
QUESTIONS = Path("questions_candidate.json")  # Keep frozen expected claims unchanged.
OUTPUT = Path("grading_sheet.csv")  # Applicant fills answer_grade and notes.
CITATION = re.compile(r"\[([^\]]+PDF p\. \d+)\]")  # Match displayed page citations.
FIELDS = [
    "request_id",
    "setting",
    "question_id",
    "question",
    "expected",
    "full_model_answer",
    "citations",
    "retrieved_pages",
    "expected_page_present",
    "answer_grade",
    "notes",
]


def comparison_rows() -> list[dict]:
    with RESULTS.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    return [
        row
        for row in rows
        if row["source"] == "real"
        and row["scenario"] == "retrieval_comparison"
        and row["retrieval_setting"] in ("small", "medium")
        and row["verifier_mode"] == "deterministic"
    ]


def expected_page_present(question: dict, passages: list) -> str:
    if question["paper"] == "none":
        return "not applicable"
    found = any(
        passage.paper == question["paper"] and passage.page == question["pdf_page"]
        for passage in passages
    )
    return "yes" if found else "no"


def grading_row(row: dict, question: dict, indexes: dict) -> dict:
    if row["question"] != question["question"]:
        raise ValueError(f"Question text differs for {row['request_id']}")
    passages = indexes[row["retrieval_setting"]].search(row["question"])
    if len(passages) != int(row["passages"]):
        raise ValueError(f"Retrieved passage count differs for {row['request_id']}")
    citations = CITATION.findall(row["answer"])
    return {
        "request_id": row["request_id"],
        "setting": row["retrieval_setting"],
        "question_id": row["question_id"],
        "question": row["question"],
        "expected": question["expected"],
        "full_model_answer": row["answer"],
        "citations": "; ".join(citations),
        "retrieved_pages": "; ".join(passage.citation for passage in passages),
        "expected_page_present": expected_page_present(question, passages),
        "answer_grade": "",
        "notes": "",
    }


def main() -> None:
    questions = {
        question["id"]: question
        for question in json.loads(QUESTIONS.read_text(encoding="utf-8"))
    }
    indexes = build_indexes(PAPER_FOLDER)
    rows = comparison_rows()
    if len(rows) != 30:
        raise ValueError(f"Expected 30 small/medium rows, found {len(rows)}")
    sheet = [grading_row(row, questions[row["question_id"]], indexes) for row in rows]
    with OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(sheet)
    print(f"Wrote {len(sheet)} ungraded rows to {OUTPUT}")


if __name__ == "__main__":
    main()
