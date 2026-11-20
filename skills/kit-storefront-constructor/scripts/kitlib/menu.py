"""Storefront menus: the nested tree a menu is edited as and the flat list the API speaks.

The API reads and writes a menu as a flat list of items linked by `parent_id` and
ordered by `display_sequence`. A write replaces the whole list, gives every item a new
id, stores `alias` exactly as sent, and checks nothing but the link targets. Everything
here is offline: it turns that list into a tree and back, compares two trees, and runs
the checks the server leaves to the client.
"""

from __future__ import annotations

import copy
import hashlib
import urllib.parse

from .common import UsageError, canonical_json, require_mapping

LINK_TYPES = ("page", "category", "collection", "external")
SYSTEM_ALIASES = ("main", "additional", "legal")
# The cabinet's menu editor allows a root level and three nested ones; the server has no limit.
MAX_DEPTH = 4
# The header shows its menu as a line of links at most this long.
HEADER_ROOT_LIMIT = 8
WORKSPACE_KIND = "menu-workspace"
SNAPSHOT_KIND = "menu-snapshot"
FORMAT_VERSION = 1
PATH_SEPARATOR = " / "


def validate_menu(value: object, label: str = "menu") -> dict[str, object]:
    """Require the shape of one menu as the API returns it."""
    menu = require_mapping(value, label)
    for key in ("id", "title"):
        if not isinstance(menu.get(key), str):
            raise UsageError(f"{label}.{key} must be a string.")
    items = menu.get("items")
    if not isinstance(items, list):
        raise UsageError(f"{label}.items must be an array.")
    for index, raw in enumerate(items):
        item = require_mapping(raw, f"{label}.items[{index}]")
        for key in ("id", "title"):
            if not isinstance(item.get(key), str):
                raise UsageError(f"{label}.items[{index}].{key} must be a string.")
        link = require_mapping(item.get("link"), f"{label}.items[{index}].link")
        if link.get("type") not in LINK_TYPES:
            raise UsageError(f"{label}.items[{index}].link.type is not a known link type.")
    return menu


def validate_menus(value: object) -> list[dict[str, object]]:
    """Require the menu list response."""
    if not isinstance(value, list):
        raise UsageError("The menu list response must be an array.")
    return [validate_menu(menu, f"menus[{index}]") for index, menu in enumerate(value)]


def menu_alias(menu: dict[str, object]) -> str:
    """The alias of a system menu, or an empty string for any other menu."""
    alias = menu.get("alias")
    return alias if isinstance(alias, str) else ""


def tree_from_items(items: list[dict[str, object]], *, describe: bool = False) -> list[dict[str, object]]:
    """Nest a flat item list by `parent_id`, siblings ordered by `display_sequence`.

    `describe` adds what the API says about each target (its title and URL) for
    reading; without it a node holds only what a write sends.
    """
    known = {item["id"] for item in items}
    children: dict[object, list[tuple[object, int, dict[str, object]]]] = {}
    for index, item in enumerate(items):
        parent = item.get("parent_id")
        if parent is not None and parent not in known:
            raise UsageError(f"Menu item {item['title']!r} names a parent that is not in the menu.")
        children.setdefault(parent, []).append((item.get("display_sequence", 0), index, item))

    reached = 0

    def build(parent: object) -> list[dict[str, object]]:
        nonlocal reached
        nodes = []
        for _, _, item in sorted(children.get(parent, []), key=lambda entry: (entry[0], entry[1])):
            reached += 1
            nodes.append(
                {
                    "title": item["title"],
                    "link": _node_link(require_mapping(item["link"], "link"), describe),
                    "children": build(item["id"]),
                }
            )
        return nodes

    tree = build(None)
    if reached != len(items):
        raise UsageError(
            f"{len(items) - reached} menu item(s) cannot be reached from the root: the stored menu "
            "has a loop. Repair it in the cabinet before editing it here."
        )
    return tree


def _node_link(link: dict[str, object], describe: bool) -> dict[str, object]:
    link_type = link["type"]
    if link_type == "external":
        return {"type": link_type, "url": link.get("link") or ""}
    node_link: dict[str, object] = {"type": link_type, "id": link.get("id") or ""}
    if describe:
        node_link["title"] = link.get("title")
        node_link["url"] = link.get("link")
    return node_link


def validate_tree(value: object, label: str = "tree") -> list[dict[str, object]]:
    """Require the structure of an edited tree and return it reduced to what a write sends."""
    if not isinstance(value, list):
        raise UsageError(f"{label} must be an array of menu items.")
    nodes = []
    for index, raw in enumerate(value):
        here = f"{label}[{index}]"
        node = require_mapping(raw, here)
        title = node.get("title")
        if not isinstance(title, str):
            raise UsageError(f"{here}.title must be a string.")
        link = require_mapping(node.get("link"), f"{here}.link")
        link_type = link.get("type")
        if not isinstance(link_type, str):
            raise UsageError(f"{here}.link.type must be a string.")
        key = "url" if link_type == "external" else "id"
        target = link.get(key, "")
        if not isinstance(target, str):
            raise UsageError(f"{here}.link.{key} must be a string.")
        nodes.append(
            {
                "title": title,
                "link": {"type": link_type, key: target},
                "children": validate_tree(node.get("children", []), f"{here}.children"),
            }
        )
    return nodes


def draft_items(tree: list[dict[str, object]]) -> list[dict[str, object]]:
    """Flatten a tree into the items of a write request.

    Item ids are keys local to the request (`1`, `1.2`): the server uses them only to
    resolve `parent_id` and stores its own.
    """
    items: list[dict[str, object]] = []

    def walk(nodes: list[dict[str, object]], parent_key: str) -> None:
        for position, node in enumerate(nodes, start=1):
            key = f"{parent_key}.{position}" if parent_key else str(position)
            link = node["link"]
            if link["type"] == "external":
                draft_link = {"type": "external", "link": link["url"]}
            else:
                draft_link = {"type": link["type"], "id": link["id"]}
            item: dict[str, object] = {
                "id": key,
                "title": node["title"],
                "display_sequence": position,
                "link": draft_link,
            }
            if parent_key:
                item["parent_id"] = parent_key
            items.append(item)
            walk(node["children"], key)

    walk(tree, "")
    return items


def draft_menu(title: str, alias: str, tree: list[dict[str, object]]) -> dict[str, object]:
    """The body of a menu write. The alias goes back exactly as it was read."""
    body: dict[str, object] = {"title": title, "items": draft_items(tree)}
    if alias:
        body["alias"] = alias
    return body


def fingerprint(title: str, alias: str, tree: list[dict[str, object]]) -> str:
    """A digest of everything a write replaces: title, alias and the tree."""
    canonical = canonical_json({"title": title, "alias": alias, "tree": validate_tree(tree)})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def menu_state(menu: dict[str, object]) -> dict[str, object]:
    """What a write replaces, taken from one menu response."""
    tree = tree_from_items(menu["items"])  # type: ignore[arg-type]
    title = str(menu["title"])
    alias = menu_alias(menu)
    return {
        "title": title,
        "tree": tree,
        "updated_at": menu.get("updated_at"),
        "fingerprint": fingerprint(title, alias, tree),
    }


def tree_depth(tree: list[dict[str, object]]) -> int:
    return max((1 + tree_depth(node["children"]) for node in tree), default=0)


def tree_size(tree: list[dict[str, object]]) -> int:
    return sum(1 + tree_size(node["children"]) for node in tree)


def link_key(link: dict[str, object]) -> str:
    target = link.get("url") if link.get("type") == "external" else link.get("id")
    return f"{link.get('type')}:{target}"


def tree_entries(
    tree: list[dict[str, object]], prefix: tuple[str, ...] = ()
) -> list[tuple[tuple[str, ...], str]]:
    """Every item as (path of titles, link): item ids change on each write, this does not."""
    entries = []
    for node in tree:
        path = prefix + (str(node["title"]),)
        entries.append((path, link_key(node["link"])))
        entries.extend(tree_entries(node["children"], path))
    return entries


def _child_order(tree: list[dict[str, object]], prefix: tuple[str, ...] = ()) -> dict[tuple[str, ...], list[str]]:
    order = {prefix: [str(node["title"]) for node in tree]}
    for node in tree:
        order.update(_child_order(node["children"], prefix + (str(node["title"]),)))
    return order


def _show_path(path: tuple[str, ...]) -> str:
    return PATH_SEPARATOR.join(path)


def diff_trees(base: list[dict[str, object]], work: list[dict[str, object]]) -> dict[str, object]:
    """What a write of `work` over `base` changes, keyed by title path.

    A renamed item reads as removed and added, together with everything under it.
    """
    base_links = dict(tree_entries(base))
    work_links = dict(tree_entries(work))
    added = [path for path in work_links if path not in base_links]
    removed = [path for path in base_links if path not in work_links]
    relinked = [path for path in work_links if path in base_links and base_links[path] != work_links[path]]
    base_order = _child_order(base)
    work_order = _child_order(work)
    reordered = []
    for parent, titles in work_order.items():
        before = base_order.get(parent)
        if before is None:
            continue
        kept = [title for title in titles if title in before]
        if kept != [title for title in before if title in titles]:
            reordered.append(parent)
    return {
        "added": [{"path": _show_path(path), "link": work_links[path]} for path in added],
        "removed": [{"path": _show_path(path), "link": base_links[path]} for path in removed],
        "relinked": [
            {"path": _show_path(path), "from": base_links[path], "to": work_links[path]} for path in relinked
        ],
        "reordered_under": [_show_path(parent) or "(root)" for parent in reordered],
    }


def diff_is_empty(diff: dict[str, object]) -> bool:
    return not any(diff.get(key) for key in ("added", "removed", "relinked", "reordered_under"))


def targets_index(links: object) -> dict[str, set[str]]:
    """Ids a menu item can link to, by link type, from the possible-links response."""
    mapping = require_mapping(links, "menu links")
    index: dict[str, set[str]] = {}
    for link_type in ("page", "category", "collection"):
        entries = mapping.get(link_type)
        if not isinstance(entries, list):
            raise UsageError(f"menu links.{link_type} must be an array.")
        index[link_type] = {str(entry.get("id")) for entry in entries if isinstance(entry, dict)}
    return index


def project_links(links: object) -> dict[str, object]:
    """Possible targets in a compact form, categories nested the way the catalog nests them."""
    mapping = require_mapping(links, "menu links")
    targets_index(mapping)
    categories = [entry for entry in mapping["category"] if isinstance(entry, dict)]  # type: ignore[union-attr]
    known = {entry.get("id") for entry in categories}

    def nest(parent: object) -> list[dict[str, object]]:
        level = [
            entry
            for entry in categories
            if (entry.get("parent_id") if entry.get("parent_id") in known else None) == parent
        ]
        level.sort(key=lambda entry: (entry.get("display_sequence") or 0, str(entry.get("title"))))
        return [
            {
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": entry.get("url"),
                "children": nest(entry.get("id")),
            }
            for entry in level
        ]

    return {
        "pages": [
            {
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": entry.get("url"),
                "alias": entry.get("alias") or None,
            }
            for entry in mapping["page"]  # type: ignore[union-attr]
            if isinstance(entry, dict)
        ],
        "categories": nest(None),
        "collections": [
            {
                "id": entry.get("id"),
                "title": entry.get("title"),
                "url": entry.get("url"),
                "is_automatic": entry.get("is_automatic"),
            }
            for entry in mapping["collection"]  # type: ignore[union-attr]
            if isinstance(entry, dict)
        ],
    }


def drop_unknown_targets(
    tree: list[dict[str, object]], targets: dict[str, set[str]], prefix: tuple[str, ...] = ()
) -> tuple[list[dict[str, object]], list[str]]:
    """Leave out items whose target is not among `targets`, each with everything under it."""
    kept: list[dict[str, object]] = []
    dropped: list[str] = []
    for node in tree:
        path = prefix + (str(node["title"]),)
        link = node["link"]
        link_type = str(link.get("type"))
        if link_type in targets and str(link.get("id")) not in targets[link_type]:
            dropped.append(_show_path(path))
            continue
        children, dropped_below = drop_unknown_targets(node["children"], targets, path)
        kept.append({"title": node["title"], "link": link, "children": children})
        dropped.extend(dropped_below)
    return kept, dropped


def _is_https(url: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def lint_tree(
    title: str,
    tree: list[dict[str, object]],
    base: list[dict[str, object]],
    targets: "dict[str, set[str]] | None" = None,
) -> dict[str, list[dict[str, str]]]:
    """Check an edited tree against what the storefront and the server accept.

    Errors block a write. A problem the menu already had before the edit is not
    reported as an error: the write does not make it worse, and refusing would block
    every unrelated change. `targets` (from `menu links`) adds the check of new links.
    """
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    known = set(tree_entries(base))

    def problem(bucket: list[dict[str, str]], code: str, path: tuple[str, ...], message: str) -> None:
        bucket.append({"code": code, "path": _show_path(path) or "(root)", "message": message})

    if not title.strip():
        problem(errors, "empty_menu_title", (), "The menu title is empty.")

    def walk(nodes: list[dict[str, object]], prefix: tuple[str, ...]) -> None:
        seen: set[str] = set()
        for node in nodes:
            node_title = str(node["title"])
            path = prefix + (node_title,)
            link = node["link"]
            if not node_title.strip():
                problem(errors, "empty_title", path, "A menu item has an empty title.")
            if node_title in seen:
                problem(errors, "duplicate_sibling", path, "Two items under one parent share this title.")
            seen.add(node_title)
            link_type = link.get("type")
            if link_type not in LINK_TYPES:
                problem(errors, "bad_link_type", path, f"Link type {link_type!r} is not one of {list(LINK_TYPES)}.")
            elif link_type == "external":
                if not _is_https(str(link.get("url", ""))):
                    problem(errors, "external_not_https", path, "An external link must be an https URL.")
            elif not link.get("id"):
                problem(errors, "missing_target", path, f"A {link_type} link needs the id of its target.")
            elif targets is not None and (path, link_key(link)) not in known:
                if str(link["id"]) not in targets.get(str(link_type), set()):
                    problem(
                        errors,
                        "unknown_target",
                        path,
                        f"{link_type} {link['id']} is not among the targets 'menu links' returns.",
                    )
            walk(node["children"], path)

    walk(tree, ())

    depth = tree_depth(tree)
    if depth > MAX_DEPTH:
        message = f"The menu is {depth} levels deep; the cabinet editor allows {MAX_DEPTH}."
        problem(errors if depth > tree_depth(base) else warnings, "too_deep", (), message)
    if len(tree) > HEADER_ROOT_LIMIT:
        problem(
            warnings,
            "many_roots",
            (),
            f"{len(tree)} root items: a header that shows the menu as a line of links fits {HEADER_ROOT_LIMIT}.",
        )
    if not tree:
        problem(warnings, "empty_menu", (), "The menu has no items.")
    return {"errors": errors, "warnings": warnings}


def new_workspace(menu: dict[str, object]) -> dict[str, object]:
    """A local workspace for one menu: the state it was read in and an editable copy."""
    state = menu_state(menu)
    return {
        "kind": WORKSPACE_KIND,
        "version": FORMAT_VERSION,
        "menu_id": menu["id"],
        "alias": menu_alias(menu),
        "base": state,
        "title": state["title"],
        "tree": copy.deepcopy(state["tree"]),
    }


def load_workspace(value: object) -> dict[str, object]:
    """Validate a workspace file; the recorded base must be exactly what was read."""
    workspace = require_mapping(value, "menu workspace")
    if workspace.get("kind") != WORKSPACE_KIND or workspace.get("version") != FORMAT_VERSION:
        raise UsageError("This file is not a menu workspace written by 'menu pull'.")
    if not isinstance(workspace.get("menu_id"), str) or not isinstance(workspace.get("title"), str):
        raise UsageError("The menu workspace has no menu_id or title.")
    alias = workspace.get("alias")
    if alias != "" and alias not in SYSTEM_ALIASES:
        raise UsageError("The menu workspace alias must stay as 'menu pull' wrote it.")
    base = require_mapping(workspace.get("base"), "menu workspace.base")
    base_title = base.get("title")
    if not isinstance(base_title, str):
        raise UsageError("The menu workspace base has no title.")
    base_tree = validate_tree(base.get("tree"), "base.tree")
    if base.get("fingerprint") != fingerprint(base_title, str(alias), base_tree):
        raise UsageError("The base of the menu workspace was edited; pull the menu again.")
    return {
        "kind": WORKSPACE_KIND,
        "version": FORMAT_VERSION,
        "menu_id": workspace["menu_id"],
        "alias": alias,
        "base": {
            "title": base_title,
            "tree": base_tree,
            "updated_at": base.get("updated_at"),
            "fingerprint": base["fingerprint"],
        },
        "title": workspace["title"],
        "tree": validate_tree(workspace.get("tree"), "tree"),
    }


def new_snapshot(menu: dict[str, object], taken_at: str) -> dict[str, object]:
    """Everything needed to write a menu back the way it was read."""
    state = menu_state(menu)
    return {
        "kind": SNAPSHOT_KIND,
        "version": FORMAT_VERSION,
        "menu_id": menu["id"],
        "alias": menu_alias(menu),
        "title": state["title"],
        "tree": state["tree"],
        "taken_at": taken_at,
    }


def load_snapshot(value: object) -> dict[str, object]:
    snapshot = require_mapping(value, "menu snapshot")
    if snapshot.get("kind") != SNAPSHOT_KIND or snapshot.get("version") != FORMAT_VERSION:
        raise UsageError("This file is not a menu snapshot written by 'menu push'.")
    if not isinstance(snapshot.get("menu_id"), str) or not isinstance(snapshot.get("title"), str):
        raise UsageError("The menu snapshot has no menu_id or title.")
    alias = snapshot.get("alias")
    if alias != "" and alias not in SYSTEM_ALIASES:
        raise UsageError("The menu snapshot alias is not a system alias.")
    return {
        "menu_id": snapshot["menu_id"],
        "alias": alias,
        "title": snapshot["title"],
        "tree": validate_tree(snapshot.get("tree"), "tree"),
        "taken_at": snapshot.get("taken_at"),
    }


def project_menu(menu: dict[str, object], *, with_tree: bool) -> dict[str, object]:
    """One menu for reading: totals for a list, the described tree for a single menu."""
    tree = tree_from_items(menu["items"], describe=with_tree)  # type: ignore[arg-type]
    projected: dict[str, object] = {
        "id": menu["id"],
        "alias": menu_alias(menu) or None,
        "title": menu["title"],
        "updated_at": menu.get("updated_at"),
        "items": tree_size(tree),
        "roots": len(tree),
        "depth": tree_depth(tree),
    }
    if with_tree:
        projected["tree"] = tree
    return projected


def select_menu(menus: list[dict[str, object]], menu_id: "str | None", alias: "str | None") -> dict[str, object]:
    """Pick one menu by id or by system alias."""
    if (menu_id is None) == (alias is None):
        raise UsageError("Name the menu with exactly one of --id and --alias.")
    if menu_id is not None:
        matches = [menu for menu in menus if menu.get("id") == menu_id]
        label = f"id {menu_id}"
    else:
        matches = [menu for menu in menus if menu_alias(menu) == alias]
        label = f"alias {alias}"
    if not matches:
        raise UsageError(f"The store has no menu with {label}; see 'menu list'.")
    if len(matches) > 1:
        raise UsageError(f"The store has {len(matches)} menus with {label}; name one by --id.")
    return matches[0]
