#!/usr/bin/env python3
"""Yandex Kit Skills self-installer — copies Яндекс.Кит skills into local AI agents.

Cross-platform (Windows / macOS / desktop Linux), Python 3 стандартная
библиотека only. This is the entry point a user (or Claude Code / Codex on their
behalf) runs after cloning the GitHub mirror:

    git clone <github-mirror-url> yandex-kit-skills
    cd yandex-kit-skills
    python3 install.py

It discovers every skill under ``skills/<name>/SKILL.md`` and installs it into
the skill directories of the detected agents:

  * Claude Code -> ``~/.claude/skills/<name>/``
  * Codex (OpenAI) -> ``~/.codex/skills/<name>/``

The copy is idempotent (an existing target is replaced), so re-running the
installer updates the skills. The installer reports nothing anywhere.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SKILLS_ROOT = PROJECT_ROOT / "skills"

# Agent -> skills directory under the user's home. Resolved with pathlib so the
# same code works on every OS.
AGENT_TARGETS = {
    "claude-code": Path.home() / ".claude" / "skills",
    "codex": Path.home() / ".codex" / "skills",
}

# What each skill lets the user ask for, in one line. Russian, like the cabinet:
# this summary is read by the shop owner, not by whoever ran the command. A skill
# missing from this map still installs — it is simply listed by name.
SKILL_SUMMARIES = {
    "yandex-kit-cabinet": "каталог, цены и остатки, заказы, промокоды, аналитика магазина",
    "kit-store-checkup": "ревизия магазина: что не продаётся, что заканчивается, какие заказы застряли",
    "kit-storefront-constructor": "витрина: страницы, секции, картинки и видео, публикация",
    "kit-custom-sections": "своя секция витрины — свёрстать и проверить, не выходя в сеть",
}

# Each of these is served by a skill in this bundle, end to end.
EXAMPLE_REQUESTS = (
    "Проверь мой магазин и скажи, что нужно исправить",
    "Какие заказы всё ещё ждут подтверждения?",
    "Найди товар по артикулу и подними его цену на 10%",
    "Добавь на главную ответы на частые вопросы",
)


def discover_skills() -> list[Path]:
    """Return the source directory of every skill that has a SKILL.md."""
    if not SKILLS_ROOT.is_dir():
        return []
    return sorted(p.parent for p in SKILLS_ROOT.glob("*/SKILL.md"))


def install_skill(skill_dir: Path, target_root: Path, *, dry_run: bool) -> Path:
    """Install one skill directory into ``target_root/<skill-name>``."""
    destination = target_root / skill_dir.name
    if dry_run:
        return destination
    target_root.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(skill_dir, destination)
    return destination


def detect_agents() -> dict[str, Path]:
    """Agents that are actually installed, judged by their home directory.

    An agent keeps its own directory under `$HOME` and creates it on first run,
    so its presence is the one signal available without launching anything. The
    skills directory inside it may not exist yet — that one we do create.
    """
    return {
        name: target
        for name, target in AGENT_TARGETS.items()
        if target.parent.is_dir()
    }


def resolve_agents(selected: list[str] | None) -> dict[str, Path]:
    if selected:
        unknown = [name for name in selected if name not in AGENT_TARGETS]
        if unknown:
            raise SystemExit(f"Unknown agent(s): {', '.join(unknown)}. Known: {', '.join(AGENT_TARGETS)}")
        return {name: AGENT_TARGETS[name] for name in selected}
    detected = detect_agents()
    if detected:
        return detected
    # Install anyway rather than end with nothing, and say so: directories will
    # appear for agents the user may not have.
    print(
        "No agent directory found under your home directory. Installing for all "
        f"supported agents ({', '.join(AGENT_TARGETS)}); use --agent to pick one.",
        file=sys.stderr,
    )
    return dict(AGENT_TARGETS)


def onboarding(installed_names: list[str]) -> str:
    """The summary a shop owner reads once the copying is done."""
    lines = [
        "",
        "═" * 68,
        "  Готово — скилы Яндекс.Кит установлены.",
        "═" * 68,
        "",
        "  Что теперь можно поручить ассистенту:",
        "",
    ]
    # Ordered by what a shop owner reaches for first, not alphabetically: the
    # installer discovers skills by directory name, which would bury the catalog.
    installed = set(installed_names)
    known = [name for name in SKILL_SUMMARIES if name in installed]
    for name in known:
        lines.append(f"    • {SKILL_SUMMARIES[name]}")
    for name in sorted(installed.difference(SKILL_SUMMARIES)):
        lines.append(f"    • {name}")
    lines += [
        "",
        "  Осталось два шага:",
        "",
        "    1. Перезапустите ассистента, чтобы он увидел новые скилы.",
        "    2. Напишите ему: «подключи мой магазин Яндекс.Кит».",
        "       Токен берётся в кабинете: Настройки → API → «Сгенерировать токен».",
        "       Если ассистент работает на этом компьютере, он откроет страницу,",
        "       где вы введёте токен. Если он работает на сервере, страницу открыть",
        "       не получится — тогда он попросит прислать токен в диалоге.",
        "",
        "  Дальше просто просите словами, например:",
        "",
    ]
    lines += [f"    «{request}»" for request in EXAMPLE_REQUESTS]
    lines += [
        "",
        "  Что уходит в Яндекс.Кит вместе с запросами: имя скила и текст вашего",
        "  запроса к ассистенту (до 1000 символов) — это помогает нам понимать,",
        "  где скилы ошибаются. Своих запросов куда-либо ещё скилы не делают.",
        "  Отключить: YANDEX_KIT_SKILLS_TELEMETRY_DISABLED=1",
        "",
        "  Скил витрины обновляет сам себя: узнав из ответа API, что вышла версия",
        "  новее, он скачивает обновление с GitHub и переустанавливает себя —",
        "  после вашей команды, никогда вместо неё.",
        "  Отключить: YANDEX_KIT_SKILLS_AUTOUPDATE_DISABLED=1",
        "",
        "  Подробности — разделы Self-update и Telemetry в README.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Install Yandex Kit Skills into local AI agents.")
    parser.add_argument(
        "--agent",
        action="append",
        choices=sorted(AGENT_TARGETS),
        help="Install only for the given agent(s). Default: every agent detected on this machine.",
    )
    parser.add_argument(
        "--skill",
        action="append",
        help="Install only the named skill(s). Default: all discovered skills.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print planned actions without copying.")
    args = parser.parse_args(argv)

    skills = discover_skills()
    if args.skill:
        wanted = set(args.skill)
        skills = [s for s in skills if s.name in wanted]
        missing = wanted - {s.name for s in skills}
        if missing:
            raise SystemExit(f"Skill(s) not found under {SKILLS_ROOT}: {', '.join(sorted(missing))}")
    if not skills:
        raise SystemExit(f"No skills found under {SKILLS_ROOT}")

    agents = resolve_agents(args.agent)

    installed_names: list[str] = []
    for skill_dir in skills:
        installed_names.append(skill_dir.name)
        for agent_name, target_root in agents.items():
            destination = install_skill(skill_dir, target_root, dry_run=args.dry_run)
            verb = "would install" if args.dry_run else "installed"
            print(f"[{agent_name}] {verb} {skill_dir.name} -> {destination}")

    print(
        f"\nDone: {len(installed_names)} skill(s) for {len(agents)} agent(s). "
        "Restart your agent to pick up new skills."
    )
    if not args.dry_run:
        print(onboarding(installed_names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
