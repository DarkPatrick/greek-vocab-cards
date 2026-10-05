from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
from pathlib import Path
from typing import Any

from .llm_client import BACKENDS, backend_settings, ensure_cli, render_prompt, run_llm
from .frequency import build_frequency_csv, normalize_word, read_frequency_csv
from .render import write_cards
from .storage import (
    append_state,
    known_forms,
    processed_lemma_outputs,
    processed_words,
    select_next_words,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
PERSON_NUMBERS = {
    (person, number)
    for person in ("first", "second", "third")
    for number in ("singular", "plural")
}
BASE_TENSES = {"present", "imperfect", "aorist", "future_continuous", "future_simple"}
# Verbs used only in some persons in everyday speech; everything else needs all six.
PERSON_EXCEPTIONS = {
    "πρέπει": {("third", "singular")},
    "πρόκειται": {("third", "singular")},
    "υπάρχω": {("third", "singular"), ("third", "plural")},
}
TENSE_EXCEPTIONS = {
    "είμαι": {"present", "imperfect", "future_continuous"},
    "έχω": {"present", "imperfect", "future_continuous"},
    "πρέπει": {"present", "imperfect", "future_continuous"},
    "πρόκειται": {"present", "imperfect"},
}


def apply_person_exceptions(item: dict[str, Any]) -> None:
    """Drop forms of persons a restricted verb does not use in everyday speech."""
    allowed = PERSON_EXCEPTIONS.get(normalize_word(item["lemma"]))
    if not allowed or "глагол" not in item["part_of_speech"].casefold():
        return
    for form in item["forms"]:
        form["grammatical_uses"] = [
            use for use in form["grammatical_uses"]
            if use["person"] == "not_applicable" or (use["person"], use["number"]) in allowed
        ]
    item["forms"] = [form for form in item["forms"] if form["grammatical_uses"]]
    for rank, form in enumerate(sorted(item["forms"], key=lambda f: f["popularity_rank"]), 1):
        form["popularity_rank"] = rank


def validate_verb_coverage(item: dict[str, Any]) -> None:
    """Reject silent gaps in the basic finite paradigm of a verb card."""
    if "глагол" not in item["part_of_speech"].casefold():
        return

    lemma = normalize_word(item["lemma"])
    omissions = {entry["tense"] for entry in item["omitted_basic_tenses"]}
    if len(omissions) != len(item["omitted_basic_tenses"]):
        raise ValueError(f"У {item['lemma']} повторяется исключённое время.")
    known_omissions = BASE_TENSES - TENSE_EXCEPTIONS.get(lemma, BASE_TENSES)
    if lemma in TENSE_EXCEPTIONS and omissions != known_omissions:
        raise ValueError(f"У {item['lemma']} неверно перечислены отсутствующие времена.")
    required_tenses = BASE_TENSES - omissions
    expected = PERSON_EXCEPTIONS.get(lemma, PERSON_NUMBERS)
    coverage = {tense: set() for tense in required_tenses}
    for form in item["forms"]:
        for use in form["grammatical_uses"]:
            if use["mood"] == "indicative" and use["tense"] in omissions:
                raise ValueError(
                    f"У {item['lemma']} заявлено отсутствие {use['tense']}, но эта форма присутствует."
                )
            if use["mood"] == "indicative" and use["tense"] in coverage:
                coverage[use["tense"]].add((use["person"], use["number"]))

    for tense, actual in coverage.items():
        missing = sorted(expected - actual)
        if missing:
            raise ValueError(
                f"У глагола {item['lemma']} неполный ряд {tense}: не хватает {missing}."
            )
        extra = sorted(actual - expected)
        if extra:
            print(f"  ! {item['lemma']}: в ряду {tense} лишние лица {extra}")


def warn_form_examples(item: dict[str, Any]) -> None:
    """Report forms that are not real spellings or are absent from their example."""
    for form in item["forms"]:
        display = form["display_form"]
        if any(mark in display for mark in ("+", "…", "...", "(")):
            print(f"  ! {item['source_word']}: «{display}» — не реальная написанная форма")
        for example in form["examples"]:
            if normalize_word(display) not in normalize_word(example["gr"]):
                print(f"  ! {item['source_word']}: «{display}» не найдена в примере «{example['gr']}»")


def load_config() -> dict[str, Any]:
    """Load project configuration."""
    return json.loads((PROJECT_ROOT / "config.json").read_text(encoding="utf-8"))


def validate_result(
    data: dict[str, Any],
    *,
    selected_words: list[str],
) -> list[dict[str, Any]]:
    """Perform deterministic checks beyond the JSON Schema."""
    raw_items = data.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("В ответе модели нет массива items.")

    expected = [normalize_word(word) for word in selected_words]
    actual = [
        normalize_word(str(item.get("source_word", "")))
        for item in raw_items
        if isinstance(item, dict)
    ]

    if len(actual) != len(expected):
        raise ValueError(
            f"Модель вернула {len(actual)} карточек вместо {len(expected)}."
        )

    if set(actual) != set(expected):
        raise ValueError(
            "Набор source_word в ответе модели отличается от выбранных слов.\n"
            f"Ожидались: {selected_words}\n"
            f"Получены: {[item.get('source_word') for item in raw_items]}"
        )

    if len(actual) != len(set(actual)):
        raise ValueError("Модель вернула дубликаты source_word.")

    items_by_word = {
        normalize_word(str(item["source_word"])): item
        for item in raw_items
    }

    ordered_items: list[dict[str, Any]] = []
    for word in selected_words:
        item = items_by_word[normalize_word(word)]
        apply_person_exceptions(item)
        ranks = [form["popularity_rank"] for form in item["forms"]]
        if len(ranks) != len(set(ranks)):
            raise ValueError(
                f"У {item['source_word']} повторяются popularity_rank."
            )
        displays = [normalize_word(form["display_form"]) for form in item["forms"]]
        if len(displays) != len(set(displays)):
            raise ValueError(
                f"У {item['source_word']} одна написанная форма повторяется в нескольких строках."
            )
        for form in item["forms"]:
            uses = form["grammatical_uses"]
            signatures = [tuple(use[key] for key in (
                "person", "number", "case", "gender", "tense", "mood", "voice"
            )) for use in uses]
            if len(signatures) != len(set(signatures)):
                raise ValueError(
                    f"У {item['source_word']} повторяется грамматический разбор формы {form['display_form']}."
                )
        greek_fields = [item["lemma"]] + [
            text for form in item["forms"]
            for text in (form["display_form"], form["lexical_form"], *(e["gr"] for e in form["examples"]))
        ]
        mixed = [text for text in greek_fields if CYRILLIC_RE.search(text)]
        if mixed:
            raise ValueError(f"У {item['source_word']} кириллица внутри греческого текста: {mixed[:3]}")
        validate_verb_coverage(item)
        warn_form_examples(item)
        ordered_items.append(item)

    return ordered_items


def generate_card(word: str, llm: dict[str, str], verify: bool) -> dict[str, Any]:
    """Generate (and optionally verify) the card for one source word."""
    schema_path = PROJECT_ROOT / "schemas" / "cards.schema.json"
    words_json = json.dumps([word], ensure_ascii=False)
    generated = run_llm(
        prompt=render_prompt(PROJECT_ROOT / "prompts" / "generate.md", {"INPUT_WORDS": words_json}),
        schema_path=schema_path,
        settings=llm,
    )
    items = validate_result(generated, selected_words=[word])
    if verify:
        verified = run_llm(
            prompt=render_prompt(
                PROJECT_ROOT / "prompts" / "verify.md",
                {
                    "INPUT_WORDS": words_json,
                    "JSON_TO_VERIFY": json.dumps({"items": items}, ensure_ascii=False, indent=2),
                },
            ),
            schema_path=schema_path,
            settings=llm,
        )
        items = validate_result(verified, selected_words=[word])
    return items[0]


def ensure_frequency(config: dict[str, Any], *, rebuild: bool = False) -> Path:
    """Ensure that the local frequency snapshot exists."""
    path = PROJECT_ROOT / "data" / "frequency.csv"
    if rebuild or not path.exists():
        print("Создаю локальный снимок частотности Greek wordfreq...")
        build_frequency_csv(
            path,
            limit=int(config["frequency_limit"]),
            language=str(config["language"]),
            wordlist=str(config["frequency_wordlist"]),
        )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate print-ready Modern Greek vocabulary tables via Codex or Claude CLI."
    )
    parser.add_argument("--count", type=int, default=None, help="Сколько новых слов обработать.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только показать следующие слова, не вызывать модель.",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Отключить второй независимый проход проверки.",
    )
    parser.add_argument(
        "--backend",
        choices=BACKENDS,
        default=None,
        help="Какой CLI вызывать (по умолчанию — backend из config.json).",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Переопределить модель выбранного backend.",
    )
    parser.add_argument(
        "--rebuild-frequency",
        action="store_true",
        help="Пересобрать data/frequency.csv из локального wordfreq.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_config()
    llm = backend_settings(config, args.backend)
    if args.model:
        llm["model"] = args.model

    if not args.dry_run:
        ensure_cli(llm["backend"])

    frequency_path = ensure_frequency(config, rebuild=args.rebuild_frequency)
    processed_path = PROJECT_ROOT / "data" / "processed.csv"
    known_forms_path = PROJECT_ROOT / "data" / "known_forms.csv"

    frequency_rows = read_frequency_csv(frequency_path)
    count = args.count or int(config["batch_size"])

    selected_rows = select_next_words(
        frequency_rows,
        processed=processed_words(processed_path),
        known=known_forms(known_forms_path),
        count=count,
    )

    if not selected_rows:
        raise SystemExit("Новых слов в локальном частотном списке не осталось.")

    selected_words = [row["source_word"] for row in selected_rows]

    print("Следующая партия:")
    for row in selected_rows:
        print(f"  {row['rank']:>5}. {row['source_word']}  (Zipf {row['zipf']})")

    if args.dry_run:
        return

    should_verify = bool(config["verify_with_second_pass"]) and not args.no_verify
    workers = int(config.get("workers", 4))
    print(f"\n{llm['backend']} / {llm['model']}: по одному слову, {workers} потока"
          + (", с перепроверкой" if should_verify else ""))

    # One call per word: a whole batch in one JSON gets truncated (verb paradigms are long).
    items_by_word: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(generate_card, word, llm, should_verify): word for word in selected_words}
        for future in as_completed(futures):
            word = futures[future]
            try:
                items_by_word[word] = future.result()
                print(f"  ✓ {word}", flush=True)
            except Exception as error:  # other words still get their cards
                failed.append(word)
                print(f"  ✗ {word}: {str(error)[:300]}", flush=True)

    if not items_by_word:
        raise SystemExit("Ни одной карточки не создано.")
    selected_rows = [row for row in selected_rows if row["source_word"] in items_by_word]
    items = [items_by_word[row["source_word"]] for row in selected_rows]

    output_files = write_cards(
        items,
        selected_rows=selected_rows,
        output_dir=PROJECT_ROOT / "output",
        existing_lemma_outputs=processed_lemma_outputs(processed_path),
    )

    # State is updated last: failed model/render runs never consume words.
    append_state(
        processed_path=processed_path,
        known_forms_path=known_forms_path,
        selected_rows=selected_rows,
        items=items,
        output_files=output_files,
    )

    print("\nГотово:")
    for item in items:
        key = normalize_word(item["source_word"])
        print(f"  {item['source_word']} → {output_files[key]}")
    if failed:
        print(f"\nНе получилось ({len(failed)}): {', '.join(failed)} — останутся в очереди на следующий запуск.")
    print("\nHTML-файлы можно открыть в браузере и печатать как A4 landscape.")


if __name__ == "__main__":
    main()
