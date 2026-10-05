from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .frequency import normalize_word


PROCESSED_FIELDS = [
    "rank",
    "source_word",
    "lemma",
    "russian",
    "part_of_speech",
    "output_file",
    "generated_at",
]

KNOWN_FORM_FIELDS = ["normalized_form", "display_form", "lemma"]


def _read_column(path: Path, column: str) -> set[str]:
    if not path.exists():
        return set()

    with path.open("r", encoding="utf-8", newline="") as file:
        return {
            normalize_word(row[column])
            for row in csv.DictReader(file)
            if row.get(column)
        }


def processed_words(path: Path) -> set[str]:
    """Return source tokens already completed."""
    return _read_column(path, "source_word")


def known_forms(path: Path) -> set[str]:
    """Return lexical forms already covered by generated cards."""
    return _read_column(path, "normalized_form")


def processed_lemma_outputs(path: Path) -> dict[str, str]:
    """Return the existing output file for each generated lemma."""
    if not path.exists():
        return {}

    outputs: dict[str, str] = {}
    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            lemma = normalize_word(row.get("lemma", ""))
            output_file = row.get("output_file", "")
            if lemma and output_file:
                outputs.setdefault(lemma, output_file)
                # Compound lemmas like "ένας, μία/μια, ένα" also match each of their parts.
                for part in re.split(r"[,/]", lemma):
                    if part.strip():
                        outputs.setdefault(part.strip(), output_file)
    return outputs


def select_next_words(
    frequency_rows: list[dict[str, str]],
    *,
    processed: set[str],
    known: set[str],
    count: int,
) -> list[dict[str, str]]:
    """Pick the next unseen ranked tokens."""
    selected: list[dict[str, str]] = []

    for row in frequency_rows:
        normalized = normalize_word(row["source_word"])
        if normalized in processed or normalized in known:
            continue
        # Single letters (ν, μ, σ' …) are letter names or elisions, not vocabulary.
        if len(normalized.strip("’'΄")) <= 1:
            continue

        selected.append(row)
        if len(selected) == count:
            return selected

    return selected


def append_state(
    *,
    processed_path: Path,
    known_forms_path: Path,
    selected_rows: list[dict[str, str]],
    items: list[dict[str, Any]],
    output_files: dict[str, str],
) -> None:
    """Append state only after all output files were created."""
    now = datetime.now(timezone.utc).isoformat()

    rank_by_word = {
        normalize_word(row["source_word"]): row["rank"]
        for row in selected_rows
    }

    with processed_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PROCESSED_FIELDS)
        if file.tell() == 0:
            writer.writeheader()
        for item in items:
            key = normalize_word(item["source_word"])
            writer.writerow(
                {
                    "rank": rank_by_word[key],
                    "source_word": item["source_word"],
                    "lemma": item["lemma"],
                    "russian": item["russian"],
                    "part_of_speech": item["part_of_speech"],
                    "output_file": output_files[key],
                    "generated_at": now,
                }
            )

    existing_known = known_forms(known_forms_path)
    rows_to_add: list[dict[str, str]] = []

    for item in items:
        candidates = [item["lemma"]]
        candidates.extend(form["lexical_form"] for form in item["forms"])

        for display_form in candidates:
            normalized = normalize_word(display_form)
            if not normalized or normalized in existing_known:
                continue
            existing_known.add(normalized)
            rows_to_add.append(
                {
                    "normalized_form": normalized,
                    "display_form": display_form,
                    "lemma": item["lemma"],
                }
            )

    with known_forms_path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=KNOWN_FORM_FIELDS)
        if file.tell() == 0:
            writer.writeheader()
        writer.writerows(rows_to_add)
