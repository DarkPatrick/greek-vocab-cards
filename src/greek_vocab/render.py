from __future__ import annotations

import html
import re
import unicodedata
from pathlib import Path
from typing import Any


CASE_RU = {
    "nominative": "именительный",
    "genitive": "родительный",
    "accusative": "винительный",
    "vocative": "звательный",
    "not_applicable": "—",
}

NUMBER_RU = {
    "singular": "ед.",
    "plural": "мн.",
    "both": "ед./мн.",
    "not_applicable": "—",
}

PERSON_RU = {
    "first": "1-е",
    "second": "2-е",
    "third": "3-е",
    "not_applicable": "—",
}

GENDER_RU = {
    "masculine": "муж.",
    "feminine": "жен.",
    "neuter": "ср.",
    "common": "общ.",
    "not_applicable": "—",
}

TENSE_RU = {
    "present": "наст.",
    "imperfect": "имперфект",
    "aorist": "аорист",
    "future_continuous": "буд. длит.",
    "future_simple": "буд. прост.",
    "present_perfect": "перфект",
    "past_perfect": "плюсквамперфект",
    "future_perfect": "буд. перфект",
    "not_applicable": "—",
}

MOOD_RU = {
    "indicative": "изъяв.",
    "subjunctive": "сослаг.",
    "imperative": "повелит.",
    "participle": "причастие",
    "gerund": "дееприч.",
    "not_applicable": "—",
}

VOICE_RU = {
    "active": "действ.",
    "mediopassive": "медиопассив",
    "not_applicable": "—",
}


def render_grammatical_uses(form: dict[str, Any]) -> str:
    """Render every exact grammatical interpretation of one written form."""
    lines: list[str] = []
    for use in form["grammatical_uses"]:
        values = [
            PERSON_RU[use["person"]],
            NUMBER_RU[use["number"]],
            CASE_RU[use["case"]],
            GENDER_RU[use["gender"]],
            TENSE_RU[use["tense"]],
            MOOD_RU[use["mood"]],
            VOICE_RU[use["voice"]],
        ]
        applicable = [value for value in values if value != "—"]
        lines.append(" · ".join(applicable) if applicable else "—")
    return "<br>".join(html.escape(line) for line in lines)


def render_examples(form: dict[str, Any], field: str) -> str:
    """Render one column of a form's examples, one example per line."""
    return "<br>".join(html.escape(example[field]) for example in form["examples"])


def slugify(value: str) -> str:
    """Create a filesystem-safe ASCII-ish slug from transliteration."""
    normalized = unicodedata.normalize("NFKD", value)
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_value).strip("-").lower()
    return slug or "word"


def render_card_html(item: dict[str, Any], *, rank: str) -> str:
    """Render one print-friendly A4 vocabulary card."""
    rows = sorted(item["forms"], key=lambda form: form["popularity_rank"])

    table_rows = "\n".join(
        f"""
        <tr>
          <td>{form["popularity_rank"]}</td>
          <td>{render_grammatical_uses(form)}</td>
          <td class="greek"><strong>{html.escape(form["display_form"])}</strong></td>
          <td>{html.escape(form["form_transliteration"])}</td>
          <td class="greek">{render_examples(form, "gr")}</td>
          <td>{render_examples(form, "transliteration")}</td>
          <td>{render_examples(form, "ru")}</td>
        </tr>
        """
        for form in rows
    )

    note = html.escape(item.get("note_ru", ""))
    omissions = item.get("omitted_basic_tenses", [])
    if omissions:
        details = "; ".join(
            f"{TENSE_RU[entry['tense']]}: {entry['reason_ru']}" for entry in omissions
        )
        note += "<br><strong>Отсутствующие базовые времена:</strong> " + html.escape(details)
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>{html.escape(item["lemma"])} — {html.escape(item["russian"])}</title>
<style>
  @page {{ size: A4 landscape; margin: 10mm; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    margin: 0;
    color: #111;
    font-size: 10.5px;
    line-height: 1.28;
  }}
  @media print {{
    thead {{ display: table-header-group; }}
    tr {{ break-inside: avoid; page-break-inside: avoid; }}
  }}
  h1 {{ margin: 0 0 2mm; font-size: 24px; }}
  .meta {{ margin-bottom: 4mm; font-size: 12px; }}
  .note {{ margin: 0 0 4mm; }}
  table {{
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
  }}
  th, td {{
    border: 1px solid #777;
    padding: 5px 6px;
    vertical-align: top;
    overflow-wrap: anywhere;
  }}
  th {{ background: #eee; font-size: 9px; }}
  .greek {{ font-size: 12px; }}
  .footer {{ margin-top: 3mm; font-size: 9px; color: #555; }}
</style>
</head>
<body>
  <h1>{html.escape(item["lemma"])} — {html.escape(item["russian"])}</h1>
  <div class="meta">
    <strong>Транслитерация:</strong> {html.escape(item["lemma_transliteration"])}
    &nbsp;·&nbsp;
    <strong>Часть речи:</strong> {html.escape(item["part_of_speech"])}
    &nbsp;·&nbsp;
    <strong>Род:</strong> {html.escape(GENDER_RU[item["gender"]])}
    &nbsp;·&nbsp;
    <strong>Частотный ранг исходной формы:</strong> {html.escape(str(rank))}
  </div>
  <p class="note">{note}</p>

  <table>
    <thead>
      <tr>
        <th style="width:3%">№</th>
        <th style="width:22%">Грамматические употребления</th>
        <th style="width:10%">Форма</th>
        <th style="width:9%">Транслитерация</th>
        <th style="width:15%">Пример</th>
        <th style="width:18%">Транслитерация примера</th>
        <th style="width:23%">Перевод</th>
      </tr>
    </thead>
    <tbody>
      {table_rows}
    </tbody>
  </table>

  <div class="footer">
    Порядок строк — практическая оценка частоты в повседневной речи, а не точная корпусная статистика.
  </div>
</body>
</html>
"""


def write_cards(
    items: list[dict[str, Any]],
    *,
    selected_rows: list[dict[str, str]],
    output_dir: Path,
    existing_lemma_outputs: dict[str, str] | None = None,
) -> dict[str, str]:
    """Write one HTML file per lemma and map every source token to it."""
    from .frequency import normalize_word

    output_dir.mkdir(parents=True, exist_ok=True)
    rank_by_word = {
        normalize_word(row["source_word"]): row["rank"]
        for row in selected_rows
    }

    written: dict[str, str] = {}
    existing_lemma_outputs = existing_lemma_outputs or {}
    # Files are numbered sequentially (00001, 00002, …) in the order cards were created;
    # the frequency rank of the source word is shown inside the card.
    next_number = 1 + max(
        (int(path.name[:5]) for path in output_dir.glob("*.html") if path.name[:5].isdigit()),
        default=0,
    )
    items_by_lemma: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        lemma_key = normalize_word(item["lemma"])
        items_by_lemma.setdefault(lemma_key, []).append(item)

    for lemma_key, lemma_items in items_by_lemma.items():
        existing = existing_lemma_outputs.get(lemma_key)
        if existing and (output_dir.parent / existing).exists():
            for item in lemma_items:
                written[normalize_word(item["source_word"])] = existing
            continue

        item = max(
            lemma_items,
            key=lambda candidate: (
                len(candidate["forms"]),
                -int(rank_by_word[normalize_word(candidate["source_word"])]),
            ),
        )
        rank = min(
            (rank_by_word[normalize_word(candidate["source_word"])] for candidate in lemma_items),
            key=int,
        )
        slug = slugify(item["lemma_transliteration"])
        filename = f"{next_number:05d}-{slug}.html"
        next_number += 1
        path = output_dir / filename
        path.write_text(render_card_html(item, rank=rank), encoding="utf-8")
        relative_path = str(path.relative_to(output_dir.parent))
        for candidate in lemma_items:
            written[normalize_word(candidate["source_word"])] = relative_path

    return written
