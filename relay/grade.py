import argparse
import csv
from pathlib import Path

from relay.run import CSV_FIELDS

VALID_GRADES = {  # Match the four labels required by the brief.
    "correct",
    "wrong",
    "refused_correctly",
    "refused_wrongly",
}


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as file:
        return list(csv.DictReader(file))


def apply_grades(results: Path, grades: Path) -> int:
    rows = read_csv(results)
    grade_rows = read_csv(grades)
    grade_ids = [row["request_id"] for row in grade_rows]
    if len(grade_ids) != len(set(grade_ids)):
        raise ValueError("Duplicate request ID in grades")
    decisions = {row["request_id"]: row["answer_grade"] for row in grade_rows}
    known = {row["request_id"] for row in rows}
    unknown = set(decisions) - known
    if unknown:
        raise ValueError(f"Unknown request IDs: {sorted(unknown)}")
    updated = 0
    for row in rows:
        grade = decisions.get(row["request_id"])
        if grade is None:
            if row["status"] != "ok":
                row["answer_grade"] = ""
            continue
        if grade not in VALID_GRADES:
            raise ValueError(f"Invalid grade for {row['request_id']}: {grade}")
        if row["status"] != "ok":
            raise ValueError(
                f"Non-answer must have blank answer_grade: {row['request_id']}"
            )
        if row["source"] != "real":
            raise ValueError(
                f"Only real model answers can be quality graded: {row['request_id']}"
            )
        row["answer_grade"] = grade
        updated += 1
    temporary = results.with_suffix(".grading.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(results)
    return updated


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    parser.add_argument("grades", type=Path)
    args = parser.parse_args()
    print(f"Applied {apply_grades(args.results, args.grades)} grades")
