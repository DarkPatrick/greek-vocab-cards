"""Regenerate one existing card after checking its complete finite paradigm."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from greek_vocab.llm_client import BACKENDS, backend_settings, ensure_cli, render_prompt, run_llm
from greek_vocab.frequency import normalize_word
from greek_vocab.main import PROJECT_ROOT, load_config, validate_result
from greek_vocab.render import render_card_html
from greek_vocab.storage import processed_lemma_outputs


PERSON_NUMBERS = {
    (person, number)
    for person in ("first", "second", "third")
    for number in ("singular", "plural")
}
BASE_TENSES = {"present", "imperfect", "aorist", "future_continuous", "future_simple"}


def check_paradigm(item: dict, *, expected_lemma: str, tenses: set[str], impersonal: bool) -> None:
    if normalize_word(item["lemma"]) != normalize_word(expected_lemma):
        raise ValueError(f"Неожиданная лемма: {item['lemma']}")
    omissions = {entry["tense"] for entry in item["omitted_basic_tenses"]}
    if omissions != BASE_TENSES - tenses:
        raise ValueError(f"Неверно перечислены отсутствующие времена: {sorted(omissions)}")

    displays: set[str] = set()
    coverage: dict[str, set[tuple[str, str]]] = {tense: set() for tense in tenses}
    for form in item["forms"]:
        display = normalize_word(form["display_form"])
        if display in displays:
            raise ValueError(f"Повтор написанной формы: {form['display_form']}")
        displays.add(display)

        uses: set[tuple[str, ...]] = set()
        for use in form["grammatical_uses"]:
            signature = tuple(use[key] for key in (
                "person", "number", "case", "gender", "tense", "mood", "voice"
            ))
            if signature in uses:
                raise ValueError(f"Повтор разбора: {form['display_form']}")
            uses.add(signature)
            if use["case"] != "not_applicable" or use["gender"] != "not_applicable":
                raise ValueError(f"У личной формы указан падеж/род: {form['display_form']}")
            if use["mood"] == "indicative" and use["tense"] in coverage:
                if expected_lemma != "είμαι" and use["voice"] not in ("active", "mediopassive"):
                    raise ValueError(f"Не указан залог: {form['display_form']}")
                coverage[use["tense"]].add((use["person"], use["number"]))

    expected = {("third", "singular")} if impersonal else PERSON_NUMBERS
    for tense, actual in coverage.items():
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(f"Неполный ряд {tense}: пропущено {missing}, лишнее {extra}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_word")
    parser.add_argument("expected_lemma")
    parser.add_argument("--tenses", required=True, help="Comma-separated required indicative tenses")
    parser.add_argument("--impersonal", action="store_true")
    parser.add_argument("--backend", choices=BACKENDS, default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    processed = PROJECT_ROOT / "data" / "processed.csv"
    with processed.open(encoding="utf-8", newline="") as file:
        matches = [row for row in csv.DictReader(file)
                   if normalize_word(row["source_word"]) == normalize_word(args.source_word)]
    if len(matches) != 1:
        raise ValueError(f"Ожидалась одна запись processed.csv для {args.source_word}")
    row = matches[0]
    if normalize_word(row["lemma"]) != normalize_word(args.expected_lemma):
        raise ValueError(f"В processed.csv другая лемма: {row['lemma']}")

    tenses = set(args.tenses.split(","))
    words_json = json.dumps([row["source_word"]], ensure_ascii=False)
    llm = backend_settings(load_config(), args.backend)
    if args.model:
        llm["model"] = args.model
    ensure_cli(llm["backend"])
    schema = PROJECT_ROOT / "schemas" / "cards.schema.json"
    guidance = (
        "\nДля этой карточки обязательно заполни следующие полные ряды изъявительного наклонения: "
        + ", ".join(sorted(tenses))
        + (". Глагол безличный: только 3-е лицо единственного числа в каждом ряду."
           if args.impersonal else ". В каждом ряду все шесть комбинаций лица и числа.")
        + " Совпадающие написания объединяй через grammatical_uses. "
        + "Один пример может иллюстрировать только одно употребление, но перечислить нужно все."
    )

    print(f"Генерация {args.expected_lemma}...", flush=True)
    generated = run_llm(
        prompt=render_prompt(PROJECT_ROOT / "prompts" / "generate.md", {"INPUT_WORDS": words_json}) + guidance,
        schema_path=schema,
        settings=llm,
    )
    items = validate_result(generated, selected_words=[row["source_word"]])
    print("Независимая проверка...", flush=True)
    verified = run_llm(
        prompt=render_prompt(PROJECT_ROOT / "prompts" / "verify.md", {
            "INPUT_WORDS": words_json,
            "JSON_TO_VERIFY": json.dumps({"items": items}, ensure_ascii=False, indent=2),
        }) + guidance,
        schema_path=schema,
        settings=llm,
    )
    item = validate_result(verified, selected_words=[row["source_word"]])[0]
    check_paradigm(item, expected_lemma=args.expected_lemma, tenses=tenses, impersonal=args.impersonal)

    relative = processed_lemma_outputs(processed)[normalize_word(item["lemma"])]
    target = PROJECT_ROOT / relative
    if not target.is_file():
        raise ValueError(f"Не найден существующий файл: {target}")
    target.write_text(render_card_html(item, rank=row["rank"]), encoding="utf-8")
    print(f"Готово: {relative}; {len(item['forms'])} уникальных написаний", flush=True)


if __name__ == "__main__":
    main()
