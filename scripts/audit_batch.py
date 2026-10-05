"""Read-only structural audit of ranked batches of existing HTML cards."""

from __future__ import annotations

import argparse
import csv
import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HEADER = [
    "№", "Грамматические употребления", "Форма", "Транслитерация",
    "Пример", "Транслитерация примера", "Перевод",
]


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in ("th", "td") and self.row is not None:
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("th", "td") and self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def selected_cards(start: int, count: int) -> list[dict[str, str]]:
    with (ROOT / "data" / "processed.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    rows.sort(key=lambda row: int(row["rank"]))
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        if row["output_file"] not in seen:
            unique.append(row)
            seen.add(row["output_file"])
    return unique[start - 1:start - 1 + count]


def audit(row: dict[str, str]) -> list[str]:
    path = ROOT / row["output_file"]
    if not path.is_file():
        return ["файл отсутствует"]
    source = path.read_text(encoding="utf-8")
    table = TableParser()
    table.feed(source)
    errors: list[str] = []
    if not table.rows or table.rows[0] != HEADER:
        return ["неожиданные заголовки таблицы"]
    if not table.rows[1:]:
        errors.append("нет строк с формами")
    forms: set[str] = set()
    for number, cells in enumerate(table.rows[1:], 1):
        if len(cells) != len(HEADER):
            errors.append(f"строка {number}: ожидалось 7 столбцов, получено {len(cells)}")
            continue
        rank, uses, form, latin, greek, example_latin, russian = cells
        if rank != str(number):
            errors.append(f"строка {number}: нарушена нумерация")
        if not form or form in forms:
            errors.append(f"строка {number}: пустая или повторная форма {form!r}")
        forms.add(form)
        if not uses or not latin:
            errors.append(f"строка {number}: не указан разбор или транслитерация формы")
        examples = [cell.split("\n") for cell in (greek, example_latin, russian)]
        if not examples[0][0] or len({len(parts) for parts in examples}) != 1:
            errors.append(f"строка {number}: примеры, транслитерации и переводы не выровнены")
        for example in examples[0]:
            if form.casefold() not in example.casefold():
                surface = form.split(" (", 1)[0]
                if surface != form and surface.casefold() in example.casefold():
                    errors.append(
                        f"строка {number}: в колонке «Форма» есть пояснение; "
                        f"написанная форма {surface!r} в примере присутствует"
                    )
                else:
                    errors.append(f"строка {number}: форма {form!r} не найдена в примере {example!r}")
    if not re.search(r"<title>[^<]+</title>", source):
        errors.append("нет заголовка HTML")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1, help="1-based first unique card")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()
    if args.start < 1 or args.count < 1:
        parser.error("start и count должны быть положительными")
    cards = selected_cards(args.start, args.count)
    for index, row in enumerate(cards, args.start):
        errors = audit(row)
        status = "OK" if not errors else f"{len(errors)} замечаний"
        print(f"{index:02d}. {row['lemma']} — {status} — {row['output_file']}")
        for error in errors:
            print(f"    - {error}")
    print(f"Проверено: {len(cards)}; без структурных замечаний: {sum(not audit(row) for row in cards)}")


if __name__ == "__main__":
    main()
