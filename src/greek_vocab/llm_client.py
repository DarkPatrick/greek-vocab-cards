from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


BACKENDS = ("codex", "claude")

# Claude Code is a coding agent by default; for card generation it only needs
# to answer with the schema-constrained JSON, so its agent prompt is replaced.
CLAUDE_SYSTEM_PROMPT = (
    "Ты — лингвистический генератор данных. Отвечай только структурированным "
    "результатом по заданной JSON Schema, без пояснений и без обращения к файлам."
)


def render_prompt(template_path: Path, replacements: dict[str, str]) -> str:
    """Fill the small explicit placeholders used by our prompt templates."""
    prompt = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        prompt = prompt.replace("{{" + key + "}}", value)
    return prompt


def backend_settings(config: dict[str, Any], backend: str | None = None) -> dict[str, str]:
    """Resolve backend name, model and effort from config.json."""
    name = backend or str(config.get("backend", "codex"))
    if name not in BACKENDS:
        raise SystemExit(f"Неизвестный backend {name!r}; допустимо: {', '.join(BACKENDS)}.")
    settings = config.get("backends", {}).get(name, {})
    if "model" not in settings or "effort" not in settings:
        raise SystemExit(f"В config.json нет backends.{name}.model / backends.{name}.effort.")
    return {"backend": name, "model": str(settings["model"]), "effort": str(settings["effort"])}


def ensure_cli(backend: str) -> None:
    """Fail early when the selected CLI is not installed."""
    if shutil.which(backend) is None:
        raise SystemExit(
            f"Команда `{backend}` не найдена. Установи CLI и выполни вход, затем повтори запуск."
        )


def run_llm(*, prompt: str, schema_path: Path, settings: dict[str, str]) -> dict[str, Any]:
    """Run the configured CLI non-interactively and return its schema-constrained JSON."""
    runner = run_claude if settings["backend"] == "claude" else run_codex
    return runner(
        prompt=prompt,
        schema_path=schema_path,
        model=settings["model"],
        effort=settings["effort"],
    )


def _require_object(value: object, backend: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(f"Ожидался JSON-объект от {backend}.")
    return value


def run_codex(
    *,
    prompt: str,
    schema_path: Path,
    model: str,
    effort: str,
) -> dict[str, Any]:
    """Run Codex CLI non-interactively and parse its schema-constrained JSON."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json",
        delete=False,
    ) as temp_file:
        output_path = Path(temp_file.name)

    command = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--config",
        f'model_reasoning_effort="{effort}"',
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
        "-",
    ]

    try:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        if completed.returncode != 0:
            raise RuntimeError(
                "Codex CLI завершился с ошибкой.\n\n"
                f"STDERR:\n{completed.stderr.strip()}\n\n"
                f"STDOUT:\n{completed.stdout.strip()}"
            )

        raw = output_path.read_text(encoding="utf-8").strip()
        if not raw:
            raise RuntimeError("Codex CLI не записал финальный JSON.")

        return _require_object(json.loads(raw), "Codex")
    finally:
        output_path.unlink(missing_ok=True)


def run_claude(
    *,
    prompt: str,
    schema_path: Path,
    model: str,
    effort: str,
) -> dict[str, Any]:
    """Run Claude Code CLI in print mode and parse its schema-constrained JSON.

    No tools, no MCP servers, no saved session; it runs from an empty temporary
    directory so no project CLAUDE.md or project hooks are picked up.
    """
    schema_doc = json.loads(schema_path.read_text(encoding="utf-8"))
    # Claude CLI's validator does not know the draft 2020-12 meta-schema URI.
    schema_doc.pop("$schema", None)
    schema = json.dumps(schema_doc, ensure_ascii=False)
    command = [
        "claude",
        "-p",
        "--model",
        model,
        "--effort",
        effort,
        "--output-format",
        "json",
        "--json-schema",
        schema,
        "--system-prompt",
        CLAUDE_SYSTEM_PROMPT,
        "--tools",
        "",
        "--strict-mcp-config",
        "--no-session-persistence",
    ]

    with tempfile.TemporaryDirectory() as workdir:
        completed = subprocess.run(
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
            cwd=workdir,
        )

    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError:
        envelope = None

    if completed.returncode != 0 or not isinstance(envelope, dict) or envelope.get("is_error"):
        raise RuntimeError(
            "Claude CLI завершился с ошибкой.\n\n"
            f"STDERR:\n{completed.stderr.strip()}\n\n"
            f"STDOUT:\n{completed.stdout.strip()[:4000]}"
        )

    structured = envelope.get("structured_output")
    if structured is None:
        raise RuntimeError(
            "Claude CLI не вернул structured_output.\n\n"
            f"result:\n{str(envelope.get('result', ''))[:4000]}"
        )
    return _require_object(structured, "Claude")
