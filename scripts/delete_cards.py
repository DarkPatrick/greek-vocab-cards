"""Delete cards by lemma, drop their state rows and renumber the rest without gaps."""

from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from greek_vocab.frequency import normalize_word  # noqa: E402

PROCESSED = ROOT / "data" / "processed.csv"
KNOWN = ROOT / "data" / "known_forms.csv"
OUTPUT = ROOT / "output"


def read(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def write(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def generation_running() -> bool:
    """A running generator appends state at the end; editing it meanwhile would race."""
    result = subprocess.run(["ps", "-Ao", "command"], capture_output=True, text=True, check=False)
    return any(
        str(ROOT) in line or "run.py" in line or "regenerate_" in line
        for line in result.stdout.splitlines()
        if "python" in line and "delete_cards.py" not in line
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lemmas", nargs="+", help="Lemmas of the cards to delete")
    parser.add_argument("--apply", action="store_true", help="Without it only shows the plan")
    args = parser.parse_args()

    if args.apply and generation_running():
        raise SystemExit("Идёт генерация — дождись её окончания и повтори.")

    p_fields, processed = read(PROCESSED)
    k_fields, known = read(KNOWN)
    wanted = {normalize_word(lemma) for lemma in args.lemmas}
    doomed = {row["output_file"] for row in processed if normalize_word(row["lemma"]) in wanted}
    found = {normalize_word(row["lemma"]) for row in processed if row["output_file"] in doomed}
    if wanted - found:
        raise SystemExit(f"Не найдены леммы: {sorted(wanted - found)}")

    remaining = sorted(
        path for path in OUTPUT.glob("*.html") if f"output/{path.name}" not in doomed
    )
    renames = {
        f"output/{path.name}": f"output/{number:05d}{path.name[5:]}"
        for number, path in enumerate(remaining, 1)
        if int(path.name[:5]) != number
    }
    print(f"Удалить: {sorted(doomed)}")
    print(f"Переименовать: {len(renames)} файлов")
    if not args.apply:
        print("Это план; для выполнения добавь --apply.")
        return

    for name in doomed:
        (ROOT / name).unlink()
    for old in renames:  # two phases so new names never collide with old ones
        (ROOT / old).rename(ROOT / (old + ".tmp"))
    for old, new in renames.items():
        (ROOT / (old + ".tmp")).rename(ROOT / new)

    processed = [row for row in processed if row["output_file"] not in doomed]
    for row in processed:
        row["output_file"] = renames.get(row["output_file"], row["output_file"])
    write(PROCESSED, p_fields, processed)
    write(KNOWN, k_fields, [row for row in known if normalize_word(row["lemma"]) not in wanted])
    print("Готово.")


if __name__ == "__main__":
    main()
