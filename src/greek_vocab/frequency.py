from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

from wordfreq import top_n_list, zipf_frequency


_GREEK_TOKEN_RE = re.compile(r"^[\u0370-\u03FF\u1F00-\u1FFF΄’']+$")


def normalize_word(value: str) -> str:
    """Normalize Unicode and case for comparisons while preserving Greek accents."""
    return unicodedata.normalize("NFC", value.strip()).casefold()


def is_greek_token(value: str) -> bool:
    """Return True for a single Greek orthographic token."""
    return bool(_GREEK_TOKEN_RE.fullmatch(value.strip()))


def build_frequency_csv(
    path: Path,
    *,
    limit: int,
    language: str = "el",
    wordlist: str = "best",
) -> None:
    """Build a deterministic local frequency snapshot using wordfreq."""
    words = top_n_list(language, limit, wordlist=wordlist)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["rank", "source_word", "zipf"])
        writer.writeheader()

        out_rank = 0
        for word in words:
            if not is_greek_token(word):
                continue

            out_rank += 1
            writer.writerow(
                {
                    "rank": out_rank,
                    "source_word": word,
                    "zipf": f"{zipf_frequency(word, language, wordlist=wordlist):.2f}",
                }
            )


def read_frequency_csv(path: Path) -> list[dict[str, str]]:
    """Read the local ranked frequency snapshot."""
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))
