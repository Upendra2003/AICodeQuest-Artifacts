"""Review Artifact generator.

Produces reports/phase-<N>-review.md in a single fixed skeleton so every
phase's report reads the same way: Summary, What was built, Tests, Demo
output, Open questions for you. This module has no phase-specific content —
each phase's demo/report script (e.g. demo_phase_0.py) imports
`generate_report` and supplies its own content.

Design note: the standing rules described this skeleton in prose (summary /
what passed / what failed / what to decide) without pasting a literal
template, so the section names below are this module's interpretation of
that description — flagged as an open question in phase-0-review.md rather
than assumed silently.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

REPORTS_DIR = Path(__file__).resolve().parents[2] / "reports"


def _render_list(items: list[str]) -> str:
    if not items:
        return "_none_"
    return "\n".join(f"- {item}" for item in items)


def _render_numbered(items: list[str]) -> str:
    if not items:
        return "_none_"
    return "\n".join(f"{i}. {item}" for i, item in enumerate(items, start=1))


def render_report(
    phase: int,
    title: str,
    config_version: str,
    summary: str,
    built: list[str],
    tests_markdown: str,
    demo_markdown: str | None,
    open_questions: list[str],
) -> str:
    """Return the rendered markdown for a phase review report (no file I/O)."""
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    parts = [
        f"# Phase {phase} Review — {title}",
        "",
        f"**Date:** {date}",
        f"**Config version:** {config_version}",
        "",
        "## Summary",
        "",
        summary.strip(),
        "",
        "## What was built",
        "",
        _render_list(built),
        "",
        "## Tests",
        "",
        tests_markdown.strip(),
        "",
    ]
    if demo_markdown:
        parts += ["## Demo output", "", demo_markdown.strip(), ""]
    parts += [
        "## Open questions for you",
        "",
        _render_numbered(open_questions),
        "",
    ]
    return "\n".join(parts)


def generate_report(
    phase: int,
    title: str,
    config_version: str,
    summary: str,
    built: list[str],
    tests_markdown: str,
    open_questions: list[str],
    demo_markdown: str | None = None,
    out_dir: Path = REPORTS_DIR,
) -> Path:
    """Render and write reports/phase-<phase>-review.md, returning its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"phase-{phase}-review.md"
    content = render_report(
        phase=phase,
        title=title,
        config_version=config_version,
        summary=summary,
        built=built,
        tests_markdown=tests_markdown,
        demo_markdown=demo_markdown,
        open_questions=open_questions,
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path
