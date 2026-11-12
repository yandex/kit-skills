"""Каталог паттернов: чтение patterns/, fingerprint, детерминированный match."""

from __future__ import annotations

import hashlib
import json
import os
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
    def looks(self) -> dict:
        """{"start": имя, "looks": [{name, title, description, settings}]} из
        looks.json; у паттерна без выбора оформления — пустой словарь."""
        path = self.dir / "looks.json"
        if not path.is_file():
            return {}
        data = read_json(path)
        return data if isinstance(data, dict) else {}

    def look(self, name: str | None) -> dict | None:
        """Вид по имени; None-имя — стартовый вид. None, если видов нет."""
        spec = self.looks
        if not spec:
            return None
        want = name or spec.get("start")
        for item in spec.get("looks") or []:
            if item.get("name") == want:
                return item
        return None

    @property
    def alternatives(self) -> list[str]:
        return [a.strip() for a in self.meta.get("alternatives", "").split(",") if a.strip()]

    @property
    def conditional_fields(self) -> dict:
        """Поля, нужные только при условии (pattern.yaml `conditional_fields`)."""
        from .conditions import parse

        return parse(self.meta.get("conditional_fields", ""))

    @property
    def demo_copy(self) -> dict[str, list[str]]:
        """{путь: [демотексты]} — фразы-заглушки каталога, которых нет в стартовых
        настройках new (`texts` в demo.json генератора; у старых каталогов файла нет).
        Типовые заголовки и подписи кнопок (`typical`) сюда не входят: магазин
        вправе выбрать их сам, совет demo-copy на них не срабатывает."""
        path = self.dir / "demo.json"
        if not path.is_file():
            return {}
        data = read_json(path)
        texts = data.get("texts") if isinstance(data, dict) else None
        return texts if isinstance(texts, dict) else {}

    @property
    def keywords(self) -> list[str]:
        return [k.strip().lower() for k in self.meta.get("keywords", "").split(",") if k.strip()]

    @property
    def alt_fields(self) -> list[tuple[str, str]]:
        """Пары `изображение=alt` из pattern.yaml (`image=imageAlt, imageMobile=imageAlt`,
        у элементов массива — `modules[].image=modules[].imageAlt`): поле alt обязано быть
        непустым, когда у изображения задан filePath (гейт G21)."""
        pairs: list[tuple[str, str]] = []
        for item in self.meta.get("alt_fields", "").split(","):
            image, sep, alt = item.partition("=")
            if sep and image.strip() and alt.strip():
                pairs.append((image.strip(), alt.strip()))
        return pairs


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


# Буквы русских окончаний (включая «к» беглого гласного: «новинки» — «новинок»).
# Хвост слова из других букв — уже другое слово («промо» и «промокод»).
_ENDING_LETTERS = frozenset("аеиоуыэюяйьмхвтгк")


def _word_match(a: str, b: str) -> bool:
    """Одно слово в разных словоформах — грубая морфология без словаря.

    Совпадают одинаковые слова и слова с общей основой от 4 букв, у которых после
    основы расходится только окончание: у короткого не больше 2 букв, у длинного не
    больше 3, и хвосты состоят из букв окончаний («соцсети» и «соцсетей», «полка» и
    «полки», «новинки» и «новинок», «телефон» и «телефоне»). Основа короче 4 букв,
    длинный хвост или хвост не из букв окончаний — разные слова («карта» и
    «карточки», «промо» и «промокод», «пост» и «постер»).
    """
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    common = len(os.path.commonprefix((a, b)))
    if common < 4 or len(short) - common > 2 or len(long_) - common > 3:
        return False
    return set(short[common:] + long_[common:]) <= _ENDING_LETTERS


# Служебные слова не судят совпадение по слову: предлог в ключе («полка с
# обложкой») иначе давал очко паттерну на любой запрос с «с». Фразой ключ
# с предлогом по-прежнему совпадает. В ключах новых паттернов их нет.
STOP_WORDS = frozenset(
    "с со в во на и для по под над из к ко о об от до за без у при про через или а не".split()
)

# Местоимения, «все», «что», «как» есть почти в любом запросе владельца («наши
# услуги», «мы в цифрах», «смотреть все»): в ключах они допустимы, но очко дают
# только внутри фразы ключа, стоящей в запросе буква в букву.
WEAK_WORDS = frozenset(
    (
        "мы нас нам нами наш наша наше наши нашего нашей нашему нашим нашими наших "
        "вы вас вам ваш ваша ваше ваши вашего вашей вашим вашими ваших все всё это что как"
    ).split()
)


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _content_words(text: str) -> list[str]:
    """Слова текста без служебных и местоимений; составное через дефис — одно слово."""
    words = (w.strip("-") for w in re.findall(r"[\w-]+", text))
    return [w for w in words if w and w not in STOP_WORDS and w not in WEAK_WORDS]


def _query_words(query_l: str) -> set[str]:
    """Слова запроса: составное через дефис даёт и себя, и части
    («лента-разделитель» находит и ключ «лента-разделитель», и ключ «лента»)."""
    words = set(_content_words(query_l))
    for word in list(words):
        if "-" in word:
            words.update(_content_words(word.replace("-", " ")))
    return words


def _has_phrase(kw: str, query_l: str) -> bool:
    """Ключ стоит в запросе целыми словами: «телефон» не находится в «телефоне»,
    «контакт» — во «вконтакте»."""
    return re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", query_l) is not None


def _concept_weight(n_words: int) -> int:
    """Вес ключа, совпавшего целиком: чем больше в нём слов, тем точнее понятие."""
    return 1 + n_words


def _score(pattern: Pattern, query_l: str, words: set[str]) -> tuple[int, int]:
    """(очки, точные слова) паттерна для запроса.

    Ключ — одно понятие. Если в запросе есть все его слова (в любой словоформе и
    порядке), ключ совпал целиком и даёт _concept_weight; сверх этого каждое слово
    запроса, найденное в ключах паттерна, даёт очко. Слово засчитывается паттерну
    один раз, и целиком совпавший ключ обязан принести хотя бы одно новое слово:
    словоформы одного слова в разных ключах («шапка, шапку, шапки») и слово,
    повторённое в нескольких ключах («товара» в четырёх), не складываются. Поэтому
    ключ из двух слов, оба из которых есть в запросе, весит больше одного общего
    слова многих паттернов («лента», «подписаться»). Второе число — слова запроса,
    совпавшие с ключом буква в букву, а не словоформой: оно разводит ничьи.
    """
    covered: set[str] = set()
    exact: set[str] = set()
    concept_words: set[str] = set()
    score = 0
    for kw in sorted(pattern.keywords, key=lambda k: -len(_content_words(_norm(k)))):
        kw = _norm(kw)
        kw_words = _content_words(kw)
        if not kw_words:
            # Ключ из одних служебных слов и местоимений («о нас») — только фразой.
            if _has_phrase(kw, query_l) and kw not in concept_words:
                score += _concept_weight(1)
                concept_words.add(kw)
            continue
        hits = [{w for w in words if _word_match(kw_word, w)} for kw_word in kw_words]
        hit = set().union(*hits)
        covered |= hit
        exact |= words & set(kw_words)
        if all(hits) and hit - concept_words:
            score += _concept_weight(len(kw_words))
            if _has_phrase(kw, query_l):
                # Местоимение или предлог в ключе очков не дают, но фраза ключа,
                # стоящая в запросе буква в букву («наши услуги», «смотреть все»),
                # засчитывает и их.
                score += len(re.findall(r"[\w-]+", kw)) - len(kw_words)
            concept_words |= hit
    return score + len(covered), len(exact)


def match(query: str) -> list[tuple[Pattern, int]]:
    """Отбор по ключевым словам: скрипт только сужает, выбор делает модель.

    Очки — ключи, совпавшие целиком, и слова запроса, покрытые ключами (_score).
    Ничью разводят слова, совпавшие буква в букву, затем алфавит.
    """
    query_l = _norm(query)
    words = _query_words(query_l)
    scored: list[tuple[Pattern, int, int]] = []
    for pattern in load_all():
        score, exact = _score(pattern, query_l, words)
        if score > 0:
            scored.append((pattern, score, exact))
    scored.sort(key=lambda x: (-x[1], -x[2], x[0].id))
    return [(pattern, score) for pattern, score, _ in scored[:3]]


def alternatives(found: list[Pattern], limit: int = 3) -> list[Pattern]:
    """Соседи по смыслу найденных паттернов (pattern.yaml `alternatives`), чтобы
    кандидатов было 2–3: модель выбирает между видами блоков, а не берёт
    единственного победителя по ключевым словам."""
    seen = {p.id for p in found}
    out: list[Pattern] = []
    for pattern in found:
        for alt in pattern.alternatives:
            if len(found) + len(out) >= limit:
                return out
            if alt in seen:
                continue
            try:
                out.append(get(alt))
            except SystemExit:
                continue
            seen.add(alt)
    return out


def identify(html: str, css: str, schema: dict) -> str | None:
    fp = fingerprint(html, css, schema)
    for pattern in load_all():
        if pattern.meta.get("fingerprint") == fp:
            return pattern.id
    return None
