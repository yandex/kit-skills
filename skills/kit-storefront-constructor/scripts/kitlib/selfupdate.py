"""Автообновление скила: сравнить свою версию с той, что назвал сервер, и переустановиться.

Механика целиком укладывается в два уже существующих обмена и не заводит третьего:

* **Проверка** — заголовок `X-Skill-Version` на ответах API, которые скил делал бы
  и так. Отдельного запроса «какая версия свежая» нет. Заголовок приходит
  безусловно, в том числе на ошибочных ответах и при отключённой телеметрии:
  отказ отдавать данные о себе не должен наказывать пользователя устаревшим
  клиентом.
* **Доставка** — публичный репозиторий `github.com/yandex/kit-skills`. Это
  единственное место во всём скиле, где открывается соединение не к API Кита, и
  исключение сделано осознанно: проверка и доставка обязаны жить в разных
  системах. Иначе отказ шлюза, который чинит обновление, блокирует само
  обновление — ровно тот bootstrap-дедлок, на котором ломаются самообновляющиеся
  клиенты.

Загрузка архива — не телеметрия: наружу не уходит ничего о пользователе, внутрь
приходит код, и происходит это только после того, как сервер назвал версию выше
нашей.

Все отказы этого модуля — мягкие. Обновление не имеет права испортить команду,
которую пользователь только что выполнил: любая ошибка гасится в одну строку в
stderr и не меняет код возврата.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from .common import SKILL_NAME, SKILL_VERSION, SKILL_VERSION_HEADER

ARCHIVE_URL = "https://github.com/yandex/kit-skills/archive/refs/heads/main.tar.gz"
AUTOUPDATE_OPT_OUT_VARIABLE = "YANDEX_KIT_SKILLS_AUTOUPDATE_DISABLED"
DOWNLOAD_TIMEOUT_SECONDS = 60
INSTALL_TIMEOUT_SECONDS = 120
# Всё дерево скилов — сотни килобайт. Потолок нужен не для точности, а чтобы
# подменённый или испорченный ответ не съел диск.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
STATE_DIRECTORY = Path.home() / ".yandex-kit-skills"
STATE_FILE = STATE_DIRECTORY / "update-state.json"

# Каталог агента -> имя, которое понимает install.py.
AGENT_DIRECTORIES = {".claude": "claude-code", ".codex": "codex"}

_latest_seen: "dict[str, str]" = {}


class _UpdateProblem(Exception):
    """Обновление не состоялось. Всегда мягкая ошибка, наружу не выходит."""


def note_response_headers(headers: "Mapping[str, str] | None") -> None:
    """Запомнить версии скилов, названные сервером в ответе.

    Вызывается на каждом ответе API — и на успешном, и на ошибочном. Само по себе
    ничего не делает и ничего не пишет на диск.
    """
    if not headers:
        return
    try:
        value = headers.get(SKILL_VERSION_HEADER)
    except Exception:  # noqa: BLE001 — источник заголовков может быть любым
        return
    if not value:
        return
    _latest_seen.update(parse_latest_header(value))


def parse_latest_header(value: str) -> "dict[str, str]":
    """Разобрать `name=version; name=version` в отображение.

    Неразбираемые куски пропускаются молча: заголовок может вырасти новыми
    полями, и клиент, падающий на незнакомом синтаксисе, превращает выкатку
    сервера в поломку у пользователя.
    """
    versions = {}
    for chunk in value.split(";"):
        name, separator, version = chunk.partition("=")
        if not separator:
            continue
        name = name.strip()
        version = version.strip()
        if name and version:
            versions[name] = version
    return versions


def parse_version(text: str) -> "tuple[int, ...] | None":
    """Разобрать `YYYY.MM.DD[.N]` в кортеж чисел, иначе None."""
    parts = text.split(".")
    numbers = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    return tuple(numbers) if numbers else None


def is_newer(candidate: str, current: str) -> bool:
    """Строго ли `candidate` новее `current`.

    Нечитаемая версия с любой стороны означает «не новее»: обновляться по
    непонятому значению опаснее, чем не обновиться.
    """
    parsed_candidate = parse_version(candidate)
    parsed_current = parse_version(current)
    if parsed_candidate is None or parsed_current is None:
        return False
    return parsed_candidate > parsed_current


def autoupdate_enabled() -> bool:
    """Разрешено ли скилу переустанавливать себя."""
    flag = os.environ.get(AUTOUPDATE_OPT_OUT_VARIABLE, "").strip().lower()
    return flag not in {"1", "true", "yes", "on"}


def maybe_update(stream: "TextIO | None" = None) -> None:
    """Обновиться, если сервер назвал версию новее нашей. Никогда не бросает."""
    output = stream if stream is not None else sys.stderr
    if not autoupdate_enabled():
        return
    try:
        target = _pending_target()
        if target is None:
            return
        if _already_attempted(target):
            # Стоп-кран. Если константа на сервере ушла вперёд публикации, без
            # него клиент будет переустанавливаться на каждый вызов и никогда не
            # догонит — а таких клиентов много, и упрутся они все в один хост.
            return
        _remember_attempt(target)
        _reinstall()
    except _UpdateProblem as problem:
        print(f"kit-skills: обновление до свежей версии не удалось ({problem}).", file=output)
    except Exception as problem:  # noqa: BLE001 — обновление не имеет права уронить команду
        print(f"kit-skills: обновление до свежей версии не удалось ({problem}).", file=output)
    else:
        print(
            f"kit-skills: скил {SKILL_NAME} обновлён с {SKILL_VERSION} до {target}. "
            "Скажи пользователю, что скил обновился и агента нужно перезапустить — "
            "инструкции текущей сессии прочитаны из прежней версии.",
            file=output,
        )


def _pending_target() -> "str | None":
    """Версия, до которой стоит обновиться, либо None."""
    latest = _latest_seen.get(SKILL_NAME)
    if not latest or not is_newer(latest, SKILL_VERSION):
        return None
    return latest


def _read_state() -> "dict[str, object]":
    try:
        raw = STATE_FILE.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def _already_attempted(target: str) -> bool:
    entry = _read_state().get(SKILL_NAME)
    return isinstance(entry, dict) and entry.get("attempted") == target


def _remember_attempt(target: str) -> None:
    """Записать попытку **до** её выполнения.

    Порядок именно такой: попытка, прерванная на середине, не должна
    повторяться бесконечно.
    """
    state = _read_state()
    state[SKILL_NAME] = {"attempted": target, "from": SKILL_VERSION}
    try:
        STATE_DIRECTORY.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        raise _UpdateProblem(f"не удалось записать {STATE_FILE}: {error}") from None


def skill_root() -> Path:
    """Каталог установленного скила (…/kit-storefront-constructor).

    Намеренно без `resolve()`: симлинк на рабочую копию нужно увидеть, а не
    развернуть.
    """
    return Path(__file__).parents[2]


def is_development_checkout(root: Path) -> bool:
    """Скил запущен из рабочей копии, а не из установленной копии.

    Два признака. Симлинк — так каталог скила выглядит у разработчика, и
    `install.py` на нём падает: `shutil.rmtree` отказывается удалять символическую
    ссылку на каталог. Второй — рядом лежит `install.py` репозитория, то есть мы
    внутри чекаута и обновлять нечего.
    """
    if root.is_symlink():
        return True
    return (root.parent.parent / "install.py").exists()


def _agent_arguments(root: Path) -> "Sequence[str]":
    """Ограничить установку тем агентом, из чьего каталога мы запущены.

    Без этого `install.py` разложит скилы всем известным агентам и заведёт
    `~/.codex/skills` пользователю, который Codex не ставил.
    """
    agent = AGENT_DIRECTORIES.get(root.parent.parent.name)
    return ("--agent", agent) if agent else ()


def _reinstall() -> None:
    root = skill_root()
    if is_development_checkout(root):
        raise _UpdateProblem("скил запущен из рабочей копии, установка пропущена")

    with tempfile.TemporaryDirectory(prefix="kit-skills-update-") as workspace:
        directory = Path(workspace)
        archive_path = directory / "kit-skills.tar.gz"
        _download(ARCHIVE_URL, archive_path)
        extracted = directory / "extracted"
        _extract(archive_path, extracted)
        installer = _find_installer(extracted)
        _run_installer(installer, _agent_arguments(root))


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream"})
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            written = 0
            with destination.open("wb") as handle:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_ARCHIVE_BYTES:
                        raise _UpdateProblem("архив неправдоподобно велик")
                    handle.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise _UpdateProblem(f"репозиторий недоступен: {error}") from None


def _extract(archive_path: Path, destination: Path) -> None:
    """Распаковать архив, проверив каждый элемент.

    `tarfile.extractall(filter=...)` появился в 3.12, а пол этого репозитория —
    3.9, поэтому проверка своя: ни одного элемента вне каталога назначения и ни
    одной ссылки или устройства.
    """
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                if member.issym() or member.islnk() or member.isdev():
                    raise _UpdateProblem(f"архив содержит небезопасный элемент {member.name}")
                target = (root / member.name).resolve()
                if target != root and root not in target.parents:
                    raise _UpdateProblem(f"архив пытается выйти за каталог: {member.name}")
            archive.extractall(destination)  # noqa: S202 — каждый элемент проверен выше
    except tarfile.TarError as error:
        raise _UpdateProblem(f"архив не читается: {error}") from None


def _find_installer(extracted: Path) -> Path:
    """Найти install.py внутри распакованного архива.

    Имя корневого каталога задаёт GitHub (`kit-skills-main`), и завязываться на
    него не стоит: ветку могут переименовать.
    """
    for candidate in sorted(extracted.iterdir()):
        installer = candidate / "install.py"
        if installer.is_file() and (candidate / "skills" / SKILL_NAME / "SKILL.md").is_file():
            return installer
    raise _UpdateProblem("в архиве нет install.py вместе со скилом")


def _run_installer(installer: Path, agent_arguments: "Sequence[str]") -> None:
    """Выполнить установку **отдельным процессом**.

    Свап каталога делает не этот процесс: он в этот момент исполняется из файлов,
    которые заменяются. На POSIX это сошло бы с рук, на Windows — нет.
    """
    command = [sys.executable, str(installer), "--skill", SKILL_NAME, *agent_arguments]
    try:
        completed = subprocess.run(  # noqa: S603 — интерпретатор и путь из временного каталога
            command,
            cwd=str(installer.parent),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=INSTALL_TIMEOUT_SECONDS,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise _UpdateProblem(f"установщик не запустился: {error}") from None
    if completed.returncode != 0:
        detail = (completed.stdout or b"").decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1] if detail else f"код {completed.returncode}"
        raise _UpdateProblem(f"установщик завершился ошибкой: {tail}")


__all__ = [
    "is_development_checkout",
    "is_newer",
    "maybe_update",
    "note_response_headers",
    "parse_latest_header",
    "parse_version",
    "skill_root",
]
