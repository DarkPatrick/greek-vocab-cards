"""Regenerate a ranked range of existing cards in place (same files, same lemmas)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_batch import selected_cards  # noqa: E402
from greek_vocab.frequency import normalize_word  # noqa: E402
from greek_vocab.llm_client import (  # noqa: E402
    BACKENDS, backend_settings, ensure_cli, render_prompt, run_llm,
)
from greek_vocab.main import PROJECT_ROOT, load_config, validate_result  # noqa: E402
from greek_vocab.render import render_card_html  # noqa: E402
from greek_vocab.storage import KNOWN_FORM_FIELDS, known_forms  # noqa: E402


PROCESSED = PROJECT_ROOT / "data" / "processed.csv"
KNOWN = PROJECT_ROOT / "data" / "known_forms.csv"
SCHEMA = PROJECT_ROOT / "schemas" / "cards.schema.json"


def regenerate(row: dict[str, str], rank: int, llm: dict[str, str], extra: str = "") -> dict[str, Any]:
    """Generate + verify one card; raise if the lemma drifted."""
    words_json = json.dumps([row["source_word"]], ensure_ascii=False)
    guidance = (
        f"\nКанонически lemma этой карточки: {row['lemma']}. Сохрани именно это написание lemma."
    )
    if extra:
        guidance += "\nОбязательные требования к этой карточке: " + extra
    generated = run_llm(
        prompt=render_prompt(PROJECT_ROOT / "prompts" / "generate.md", {"INPUT_WORDS": words_json}) + guidance,
        schema_path=SCHEMA,
        settings=llm,
    )
    items = validate_result(generated, selected_words=[row["source_word"]])
    verified = run_llm(
        prompt=render_prompt(PROJECT_ROOT / "prompts" / "verify.md", {
            "INPUT_WORDS": words_json,
            "JSON_TO_VERIFY": json.dumps({"items": items}, ensure_ascii=False, indent=2),
        }) + guidance,
        schema_path=SCHEMA,
        settings=llm,
    )
    item = validate_result(verified, selected_words=[row["source_word"]])[0]
    if normalize_word(item["lemma"]) != normalize_word(row["lemma"]):
        raise ValueError(f"лемма изменилась: {row['lemma']} → {item['lemma']}")
    (PROJECT_ROOT / row["output_file"]).write_text(
        render_card_html(item, rank=str(rank)), encoding="utf-8"
    )
    return item


def update_state(done: dict[str, dict[str, Any]]) -> None:
    """Refresh russian/part_of_speech in processed.csv and append new known forms."""
    with PROCESSED.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        fields = list(reader.fieldnames or [])
        rows = list(reader)
    for row in rows:
        item = done.get(row["output_file"])
        if item:
            row["russian"] = item["russian"]
            row["part_of_speech"] = item["part_of_speech"]
    with PROCESSED.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    existing = known_forms(KNOWN)
    new_rows = []
    for item in done.values():
        for value in [item["lemma"], *(form["lexical_form"] for form in item["forms"])]:
            key = normalize_word(value)
            if key and key not in existing:
                existing.add(key)
                new_rows.append({"normalized_form": key, "display_form": value, "lemma": item["lemma"]})
    with KNOWN.open("a", encoding="utf-8", newline="") as file:
        csv.DictWriter(file, fieldnames=KNOWN_FORM_FIELDS).writerows(new_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, required=True, help="1-based first unique card")
    parser.add_argument("--count", type=int, required=True)
    parser.add_argument("--only", default="", help="Comma-separated lemmas to restrict to (retries)")
    parser.add_argument(
        "--guidance",
        type=Path,
        default=None,
        help="JSON {lemma: extra requirements}; only these lemmas are regenerated",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--backend", choices=BACKENDS, default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    llm = backend_settings(load_config(), args.backend)
    if args.model:
        llm["model"] = args.model
    ensure_cli(llm["backend"])

    with PROCESSED.open(encoding="utf-8", newline="") as file:
        all_rows = list(csv.DictReader(file))
    cards = selected_cards(args.start, args.count)
    if args.only:
        wanted = {normalize_word(value) for value in args.only.split(",")}
        cards = [card for card in cards if normalize_word(card["lemma"]) in wanted]
    extra: dict[str, str] = {}
    if args.guidance:
        extra = {
            normalize_word(lemma): text
            for lemma, text in json.loads(args.guidance.read_text(encoding="utf-8")).items()
        }
        cards = [card for card in cards if normalize_word(card["lemma"]) in extra]
    ranks = {
        card["output_file"]: min(int(r["rank"]) for r in all_rows if r["output_file"] == card["output_file"])
        for card in cards
    }

    print(f"{len(cards)} карточек · {llm['backend']} / {llm['model']} · {args.workers} потока", flush=True)
    started = time.time()
    done: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                regenerate, card, ranks[card["output_file"]], llm, extra.get(normalize_word(card["lemma"]), "")
            ): card
            for card in cards
        }
        for future in as_completed(futures):
            card = futures[future]
            minutes = (time.time() - started) / 60
            try:
                item = future.result()
            except Exception as error:  # keep going; failures are retried with --only
                failed.append(card["lemma"])
                print(f"[{minutes:5.1f}m] ✗ {card['lemma']}: {str(error)[:300]}", flush=True)
                continue
            done[card["output_file"]] = item
            print(f"[{minutes:5.1f}m] ✓ {card['lemma']} — {len(item['forms'])} форм", flush=True)

    update_state(done)
    print(f"Готово: {len(done)}; ошибок: {len(failed)}", flush=True)
    if failed:
        print("Повторить: --only " + ",".join(failed), flush=True)


if __name__ == "__main__":
    main()
