"""Policy claims a section prints: advantages of the product card and the cart, the free-delivery line.

The storefront prints a platform wording for an advantage without its own `text`, whatever
the store's payment and delivery settings, and starter templates seed slots with texts of
another business. Both are promises the merchant has to confirm.
"""

from __future__ import annotations

import re

from designlib.common import get_path

PD = "YandexKit.ProductDetails"
CART = "YandexKit.ShoppingCart"
SLOTS = 6

# The storefront's own wording for an advantage without `text` (where known).
PLATFORM_WORDING = {
    "delivery": "Доставка в пункты выдачи или до двери",
    "return": "Удобный возврат",
    "payment": "Оплата — картой, СБП или наличными",
}
# Types that promise a service only some stores give.
CONFIRM_TYPES = {
    "fitting": "try-on: only wearables with try-on",
    "refusal": "refusal after try-on: only wearables with try-on",
    "split-payment": "split payment: only with Split connected",
}
# Starter-template phrases that belong to another business.
TEMPLATE_TEXT = re.compile(r"маркет|примерк|оплаивайте|после примерки", re.I)


def advantages(widget: str, settings: "dict[str, object]") -> "list[tuple[str, str, str]]":
    """(slot, type, what it prints) for every advantage that renders on the card or the cart."""
    if widget not in (PD, CART):
        return []
    block = get_path(settings, "modules.advantages")
    if not isinstance(block, dict) or block.get("enableAdvantages") is False:
        return []
    found = []
    for number in range(1, SLOTS + 1):
        slot = block.get(f"advantage{number}")
        if not isinstance(slot, dict):
            continue
        kind = slot.get("type")
        if not isinstance(kind, str) or kind in ("", "none"):
            continue
        text = slot.get("text")
        shown = text.strip() if isinstance(text, str) and text.strip() else ""
        found.append((f"advantage{number}", kind, shown))
    return found


def advantage_concerns(widget: str, settings: "dict[str, object]") -> "list[str]":
    """Advantages that make a claim the merchant has to confirm, one line each."""
    lines = []
    for slot, kind, text in advantages(widget, settings):
        if not text:
            wording = PLATFORM_WORDING.get(kind)
            lines.append(f"{slot} ({kind}) prints the platform wording" + (f" «{wording}»" if wording else "") + ": give it the merchant's own text or set it to none")
        elif TEMPLATE_TEXT.search(text):
            lines.append(f"{slot} ({kind}) carries a starter template's text «{text[:50]}»: rewrite it from the merchant's facts or set it to none")
        if kind in CONFIRM_TYPES:
            lines.append(f"{slot} is {CONFIRM_TYPES[kind]}: confirm with the seller")
    return lines


DELIVERY_PROGRESS = (
    "$.delivery.enableProgress is on: the storefront prints «До бесплатной доставки: N ₽» under the buy button and in the cart "
    "whenever the backend reports a threshold. Keep it only with a free-delivery threshold the merchant confirmed; otherwise write false."
)
