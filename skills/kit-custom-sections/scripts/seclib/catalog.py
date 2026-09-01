"""Каталог паттернов: чтение patterns/, fingerprint, детерминированный match."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .common import PATTERNS_DIR, SectionError, parse_flat_yaml, read_json


def fingerprint(html: str, css: str, schema: dict) -> str:
    payload = json.dumps(
        {"html": html, "css": css, "schema": schema}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Pattern:
    def __init__(self, pdir: Path) -> None:
        self.dir = pdir
        self.meta = parse_flat_yaml((pdir / "pattern.yaml").read_text(encoding="utf-8"))
        self.id = self.meta.get("id", pdir.name)

    @property
    def html(self) -> str:
        return (self.dir / "template.hbs").read_text(encoding="utf-8")

    @property
    def css(self) -> str:
        return (self.dir / "styles.css").read_text(encoding="utf-8")

    @property
    def schema(self) -> dict:
        return read_json(self.dir / "schema.json")

    @property
    def answers_schema(self) -> dict:
        return read_json(self.dir / "answers.schema.json")

    @property
    def defaults(self) -> dict:
        return read_json(self.dir / "defaults.json")

    @property
    def keywords(self) -> list[str]:
        return [k.strip().lower() for k in self.meta.get("keywords", "").split(",") if k.strip()]


def load_all() -> list[Pattern]:
    if not PATTERNS_DIR.is_dir():
        return []
    return sorted(
        (Pattern(p) for p in PATTERNS_DIR.iterdir() if (p / "pattern.yaml").is_file()),
        key=lambda p: p.id,
    )


def get(pattern_id: str) -> Pattern:
    pdir = PATTERNS_DIR / pattern_id
    if not (pdir / "pattern.yaml").is_file():
        raise SectionError(f"паттерн {pattern_id!r} не найден в каталоге", code=5)
    return Pattern(pdir)


def _word_match(a: str, b: str) -> bool:
    """Точное совпадение или общий префикс >= 5 символов (грубая морфология:
    «преимуществ» из запроса должно находить ключ «преимущества»)."""
    if a == b:
        return True
    return min(len(a), len(b)) >= 5 and (a.startswith(b) or b.startswith(a))


def match(query: str) -> list[tuple[Pattern, int]]:
    """Отбор по ключевым словам: скрипт только сужает, выбор делает модель."""
    words = set(re.findall(r"[\wЀ-ӿ-]+", query.lower()))
    scored: list[tuple[Pattern, int]] = []
    for pattern in load_all():
        score = 0
        for kw in pattern.keywords:
            kw_words = kw.split()
            if kw in query.lower():
                score += 3
            elif any(_word_match(kw_word, w) for kw_word in kw_words for w in words):
                score += 1
        if score > 0:
            scored.append((pattern, score))
    scored.sort(key=lambda x: (-x[1], x[0].id))
    return scored[:3]


def identify(html: str, css: str, schema: dict) -> str | None:
    fp = fingerprint(html, css, schema)
    for pattern in load_all():
        if pattern.meta.get("fingerprint") == fp:
            return pattern.id
    return None
