"""Deterministic quality-control scan: flag stub/placeholder/hack markers in a
worker's output so incomplete implementations get caught before (and on top of)
the orchestrator's LLM review. No LLM, no quota — pure regex over files.

Patterns live in config/quality.yml, so the rubric is tunable without code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

# Fallback defaults if config/quality.yml is absent, so `qc` always does something.
_DEFAULT_MARKERS = (
    r"TODO", r"FIXME", r"XXX", r"HACK", r"\bstub\b",
    r"not[ _-]?implemented", r"NotImplementedError", r"placeholder",
    r"your code here", r"^\s*\.\.\.\s*$",
)
_DEFAULT_EXCLUDE = (
    ".md", ".txt", "test_", "_test.", "/tests/",
    "/.venv/", "/node_modules/", "__pycache__", ".pyc", "/.git/",
)


@dataclass(frozen=True, slots=True)
class Finding:
    file: str
    line: int
    marker: str
    text: str


@dataclass(frozen=True, slots=True)
class QualityConfig:
    markers: tuple[re.Pattern[str], ...]
    exclude: tuple[str, ...]

    @classmethod
    def load(cls, root: str | Path) -> "QualityConfig":
        path = Path(root) / "quality.yml"
        raw = yaml.safe_load(path.read_text()) if path.exists() else {}
        raw = raw or {}
        markers = tuple(raw.get("stub_markers", _DEFAULT_MARKERS)) or _DEFAULT_MARKERS
        exclude = tuple(raw.get("exclude", _DEFAULT_EXCLUDE))
        return cls(
            markers=tuple(re.compile(m, re.IGNORECASE) for m in markers),
            exclude=exclude,
        )


def _excluded(f: Path, exclude: tuple[str, ...]) -> bool:
    # Directory-ish tokens (contain a slash, or are pycache) match the full path;
    # everything else matches only the file name, so a "test_" token doesn't drop
    # a whole tree just because a parent dir happens to contain "test_".
    name, full = f.name, str(f)
    for tok in exclude:
        if ("/" in tok or tok == "__pycache__"):
            if tok in full:
                return True
        elif tok in name:
            return True
    return False


def _iter_files(paths: list[Path], exclude: tuple[str, ...]):
    for p in paths:
        candidates = p.rglob("*") if p.is_dir() else [p]
        for f in candidates:
            if f.is_file() and not _excluded(f, exclude):
                yield f


def scan(paths: list[str | Path], cfg: QualityConfig) -> list[Finding]:
    """Return every stub/hack marker hit across the given files/directories."""
    findings: list[Finding] = []
    for f in _iter_files([Path(p) for p in paths], cfg.exclude):
        try:
            lines = f.read_text(errors="replace").splitlines()
        except (OSError, UnicodeError):
            continue  # binary / unreadable — nothing to review
        for n, line in enumerate(lines, 1):
            for pat in cfg.markers:
                if pat.search(line):
                    findings.append(
                        Finding(file=str(f), line=n, marker=pat.pattern, text=line.strip()[:160])
                    )
                    break  # one finding per line is enough
    return findings
