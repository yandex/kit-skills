"""Local write workspace: pull snapshot, manifest, offline edits, and offline diff.

The workspace holds the full ConstructorContent so the agent never carries the ~660 KB
payload in its context. Every editing command mutates `work.json` only; the pristine base
snapshot and its manifest stay untouched until the next `content pull`.

Layout for a workspace at WORK:
    WORK                    — editable working copy (full ConstructorContent as read)
    WORK.base.json          — pristine base snapshot from the same pull
    WORK.manifest.json      — base_version_id, sha256 of the base snapshot, source, counts
"""

from __future__ import annotations

import copy
import datetime
import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from kitlib.common import (
    UsageError,
    canonical_json,
    content_version_id,
    require_mapping,
    validate_content,
    validated_pages,
)

BASE_SUFFIX = ".base.json"
MANIFEST_SUFFIX = ".manifest.json"
COMPOSITE_SECTION_TYPES = {"parent", "child"}
MYSTIQUE_WIDGET = "YandexKit.Mystique"


def default_work_path(base_url: str) -> Path:
    """Default per-store workspace path next to the token directory."""
    configured = os.environ.get("KIT_WORKSPACE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser() / "work.json"
    host = re.sub(r"^https?://", "", base_url).split("/", 1)[0].split(":", 1)[0]
    key = re.sub(r"[^A-Za-z0-9.-]", "_", host) or "default"
    return Path.home() / ".yandex-kit-skills" / "workspace" / key / "work.json"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def count_sections(content: "dict[str, object]") -> int:
    """Total sections across all page layouts."""
    return sum(len(page.get("layout") or []) for page in validated_pages(content))


def write_workspace(
    work_path: Path,
    content: "dict[str, object]",
    source_base_url: str,
    schema_build_version: "str | None" = None,
) -> "dict[str, object]":
    """Persist a fresh pull: working copy, base snapshot, and manifest."""
    validate_content(content, "Pulled content")
    work_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = canonical_json(content)
    work_path.write_text(serialized, encoding="utf-8")
    base_path = Path(str(work_path) + BASE_SUFFIX)
    base_path.write_text(serialized, encoding="utf-8")
    manifest = {
        "base_version_id": content_version_id(content),
        "base_sha256": _sha256_text(serialized),
        "pulled_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_base_url": source_base_url,
        "pages_count": len(validated_pages(content)),
        "sections_count": count_sections(content),
    }
    if schema_build_version:
        manifest["schema_build_version"] = schema_build_version
    manifest_path = Path(str(work_path) + MANIFEST_SUFFIX)
    manifest_path.write_text(canonical_json(manifest), encoding="utf-8")
    return manifest


class Workspace:
    """One loaded workspace: working copy, base snapshot, and validated manifest."""

    def __init__(self, work_path: Path, content: "dict[str, object]", base: "dict[str, object]", manifest: "dict[str, object]"):
        self.work_path = work_path
        self.content = content
        self.base = base
        self.manifest = manifest

    @property
    def base_version_id(self) -> int:
        return int(self.manifest["base_version_id"])  # validated in load()

    def save(self) -> None:
        """Persist the working copy only; base and manifest stay pristine."""
        self.work_path.write_text(canonical_json(self.content), encoding="utf-8")


def load_workspace(work_path: Path) -> Workspace:
    """Load and validate a workspace, failing closed on a broken base or manifest."""
    if not work_path.is_file():
        raise UsageError(f"Workspace file {work_path} does not exist; run 'content pull' first.")
    base_path = Path(str(work_path) + BASE_SUFFIX)
    manifest_path = Path(str(work_path) + MANIFEST_SUFFIX)
    for path, label in ((base_path, "base snapshot"), (manifest_path, "manifest")):
        if not path.is_file():
            raise UsageError(f"Workspace {label} {path} is missing; run 'content pull' again.")
    try:
        content = json.loads(work_path.read_text(encoding="utf-8"))
        base_text = base_path.read_text(encoding="utf-8")
        base = json.loads(base_text)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise UsageError(f"Cannot read workspace {work_path}: {error}") from None
    content = validate_content(content, "Workspace content")
    base = validate_content(base, "Workspace base snapshot")
    manifest = require_mapping(manifest, "Workspace manifest")
    base_version = manifest.get("base_version_id")
    if isinstance(base_version, bool) or not isinstance(base_version, int) or base_version < 1:
        raise UsageError("Workspace manifest.base_version_id must be a positive integer.")
    recorded_sha = manifest.get("base_sha256")
    if recorded_sha != _sha256_text(base_text):
        raise UsageError(
            "Workspace base snapshot does not match its manifest digest; "
            "the base was modified after pull. Run 'content pull' again."
        )
    if content_version_id(base) != base_version:
        raise UsageError("Workspace base snapshot version does not match the manifest.")
    return Workspace(work_path, content, base, manifest)


def rebase_after_push(
    workspace: Workspace,
    active_version_id: int,
    published: bool,
    version_info: "dict[str, object] | None" = None,
) -> "dict[str, object]":
    """Move the workspace base onto the version active after a successful push.

    Without this, the very next push would carry a stale ``base_version_id`` and hit 409.

    * ``published`` (activate: true, or an old server that published regardless): the
      written working copy IS the active content now, so it becomes the new base.
    * not published (activate: false honored): the active version is the server-made
      copy of the previous content, so the base keeps its content and only moves its
      version id to ``active_version_id``; the working copy still holds the proposal,
      and its diff against the base remains exactly the unpublished change.
    """
    if published:
        new_base = copy.deepcopy(workspace.content)
        version = copy.deepcopy(version_info) if version_info is not None else dict(new_base["version"])  # type: ignore[arg-type]
        version["id"] = active_version_id
        new_base["version"] = version
        workspace.content["version"] = copy.deepcopy(version)
        workspace.save()
    else:
        new_base = copy.deepcopy(workspace.base)
        # Only the id is authoritative: the copy-version's author/message live server-side.
        version = dict(new_base["version"])  # type: ignore[arg-type]
        version["id"] = active_version_id
        new_base["version"] = version
    workspace.base = new_base
    serialized = canonical_json(new_base)
    Path(str(workspace.work_path) + BASE_SUFFIX).write_text(serialized, encoding="utf-8")
    manifest = dict(workspace.manifest)
    manifest["base_version_id"] = active_version_id
    manifest["base_sha256"] = _sha256_text(serialized)
    manifest["rebased_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["pages_count"] = len(validated_pages(new_base))
    manifest["sections_count"] = count_sections(new_base)
    workspace.manifest = manifest
    Path(str(workspace.work_path) + MANIFEST_SUFFIX).write_text(canonical_json(manifest), encoding="utf-8")
    return manifest


# --- selection helpers ----------------------------------------------------------------


def select_page(content: "dict[str, object]", alias: "str | None", page_id: "str | None") -> "dict[str, object]":
    """Select exactly one page by exact alias or UUID."""
    pages = validated_pages(content)
    if alias is not None:
        matches = [page for page in pages if page.get("alias") == alias]
        selector = f"alias {alias!r}"
    else:
        matches = [page for page in pages if page["id"] == page_id]
        selector = f"id {page_id!r}"
    if not matches:
        raise UsageError(f"Page with {selector} was not found.")
    if len(matches) > 1:
        raise UsageError(f"Page selector {selector} is ambiguous.")
    return matches[0]


def find_section_copies(
    content: "dict[str, object]",
    section_id: str,
) -> "list[tuple[dict[str, object], int, dict[str, object]]]":
    """Return every (page, layout index, section) copy of one section id."""
    copies = []
    for page in validated_pages(content):
        layout = page.get("layout") or []
        for index, item in enumerate(layout):
            if isinstance(item, dict) and item.get("id") == section_id:
                copies.append((page, index, item))
    return copies


def require_section_copies(content: "dict[str, object]", section_id: str):
    copies = find_section_copies(content, section_id)
    if not copies:
        raise UsageError(f"Section with id {section_id!r} was not found on any page.")
    return copies


def section_template(content: "dict[str, object]", template_id: str) -> "dict[str, object]":
    """Find one section template by UUID in the snapshot catalog."""
    templates = content.get("section_templates")
    templates = templates if isinstance(templates, list) else []
    matches = [
        template
        for template in templates
        if isinstance(template, dict) and template.get("id") == template_id
    ]
    if not matches:
        raise UsageError(f"Section template {template_id!r} is not in the pulled catalog.")
    return matches[0]


def settings_diff(old: object, new: object) -> "dict[str, object]":
    """Top-level micro-diff between two settings objects."""
    old_map = old if isinstance(old, dict) else {}
    new_map = new if isinstance(new, dict) else {}
    return {
        "added": sorted(set(new_map) - set(old_map)),
        "removed": sorted(set(old_map) - set(new_map)),
        "changed": sorted(
            key for key in set(old_map) & set(new_map) if old_map[key] != new_map[key]
        ),
    }


def renumber_layout(layout: "list[object]") -> None:
    """Rewrite display_sequence as the 1-based list order."""
    for index, section in enumerate(layout):
        if isinstance(section, dict):
            section["display_sequence"] = index + 1


def assign_sequence(layout: "list[object]", index: int) -> None:
    """Give layout[index] a display_sequence between its neighbours.

    Prefers an unused integer in the neighbour gap so untouched sections keep their
    server-assigned sequence (smallest possible write diff); renumbers the whole layout
    only when the gap is exhausted.
    """
    section = layout[index]
    assert isinstance(section, dict)
    previous = layout[index - 1] if index > 0 else None
    following = layout[index + 1] if index + 1 < len(layout) else None
    low = previous.get("display_sequence") if isinstance(previous, dict) else None
    high = following.get("display_sequence") if isinstance(following, dict) else None
    if not isinstance(low, int):
        low = None
    if not isinstance(high, int):
        high = None
    if low is None and high is None:
        section["display_sequence"] = 1
        return
    if high is None:
        assert low is not None
        section["display_sequence"] = low + 1
        return
    if low is None:
        if high > 0:
            section["display_sequence"] = high - 1
            return
    elif high - low > 1:
        section["display_sequence"] = low + 1
        return
    renumber_layout(layout)
    return


def ensure_unique_sequences(layout: "list[object]") -> None:
    """Renumber if display_sequence values collide (unordered server layout edge case)."""
    sequences = [
        section.get("display_sequence") for section in layout if isinstance(section, dict)
    ]
    if len(sequences) != len(set(sequences)):
        renumber_layout(layout)


# --- edit operations -------------------------------------------------------------------


def set_section_settings(
    workspace: Workspace,
    section_id: str,
    new_settings: "dict[str, object]",
    new_template_id: "str | None" = None,
) -> "dict[str, object]":
    """Replace settings of one section, keeping composite copies in sync.

    With ``new_template_id`` the section is also rebound to another custom template,
    which is how an already-published custom section is edited: its markup lives in a
    template that must never be edited in place, so a new one is created and the
    section moves onto it. Settings are mandatory here for a reason — the caller has
    to say what the fields hold under the new template rather than leave the old
    values to land in a shape that may no longer have room for them.

    A composite section (header/footer) is stored once per storefront but appears as a
    copy on every page; the backend takes the parent copy's settings, so all copies are
    rewritten together — otherwise a child-copy edit would be silently lost.

    An existing section's ``settings.version`` is preserved: it may be omitted in the
    new settings (carried over) or repeated unchanged, but never altered — changing it
    would be a client-side migration, which this skill does not perform.
    """
    copies = require_section_copies(workspace.content, section_id)
    if new_template_id is not None:
        section_template(workspace.base, new_template_id)
    old_settings = copies[0][2].get("settings")
    old_version = old_settings.get("version") if isinstance(old_settings, dict) else None
    new_version = new_settings.get("version")
    if old_version is not None:
        if new_version is None:
            new_settings = dict(new_settings)
            new_settings["version"] = old_version
        elif new_version != old_version:
            raise UsageError(
                f"settings.version must stay {old_version!r}; changing it ({new_version!r}) "
                "would be a client-side migration, which is not supported."
            )
    elif new_version is not None:
        raise UsageError(
            "The existing section carries no settings.version; adding one is not supported."
        )
    diff = settings_diff(old_settings, new_settings)
    old_template_id = copies[0][2].get("template_id")
    for _page, _index, section in copies:
        section["settings"] = copy.deepcopy(new_settings)
        # Every copy moves together: a section left on the old template on one page
        # would keep rendering the old markup there, with nothing to point at it.
        if new_template_id is not None:
            section["template_id"] = new_template_id
    report: "dict[str, object]" = {
        "section_id": section_id,
        "widget": copies[0][2].get("widget"),
        "pages_updated": [
            {"page_id": page["id"], "page_alias": page.get("alias"), "section_type": section.get("section_type")}
            for page, _index, section in copies
        ],
        "settings_diff": diff,
    }
    if new_template_id is not None:
        report["template_rebound"] = {"from": old_template_id, "to": new_template_id}
    return report


def add_section(
    workspace: Workspace,
    page: "dict[str, object]",
    widget: str,
    settings: "dict[str, object]",
    template_id: "str | None",
    position: "int | None",
) -> "dict[str, object]":
    """Insert one new usual section into a page layout at a 1-based position."""
    layout = page.get("layout")
    if not isinstance(layout, list):
        raise UsageError("Selected page has no layout array.")
    section: "dict[str, object]" = {
        "id": str(uuid.uuid4()),
        "instance_id": str(uuid.uuid4()),
        "widget": widget,
        "section_type": "usual",
        "settings": copy.deepcopy(settings),
        "display_sequence": 0,  # rewritten by renumber below
    }
    if template_id is not None:
        section["template_id"] = template_id
    index = len(layout) if position is None else max(0, min(position - 1, len(layout)))
    layout.insert(index, section)
    assign_sequence(layout, index)
    ensure_unique_sequences(layout)
    return {
        "section_id": section["id"],
        "widget": widget,
        "page_id": page["id"],
        "page_alias": page.get("alias"),
        "position": index + 1,
        "layout_count": len(layout),
    }


def remove_section(workspace: Workspace, section_id: str) -> "dict[str, object]":
    """Remove every copy of one section id; composite copies disappear together."""
    copies = require_section_copies(workspace.content, section_id)
    pages_affected = []
    for page, _index, section in copies:
        layout = page["layout"]
        assert isinstance(layout, list)
        layout[:] = [item for item in layout if not (isinstance(item, dict) and item.get("id") == section_id)]
        pages_affected.append({"page_id": page["id"], "page_alias": page.get("alias")})
    return {
        "section_id": section_id,
        "widget": copies[0][2].get("widget"),
        "removed_copies": len(copies),
        "pages_affected": pages_affected,
    }


def move_section(
    workspace: Workspace,
    section_id: str,
    position: int,
    alias: "str | None" = None,
    page_id: "str | None" = None,
) -> "dict[str, object]":
    """Move one section to a 1-based position inside its page layout."""
    copies = require_section_copies(workspace.content, section_id)
    if alias is not None or page_id is not None:
        page = select_page(workspace.content, alias, page_id)
        copies = [(p, i, s) for p, i, s in copies if p["id"] == page["id"]]
        if not copies:
            raise UsageError(f"Section {section_id!r} is not on the selected page.")
    if len(copies) > 1:
        raise UsageError(
            f"Section {section_id!r} appears on {len(copies)} pages; "
            "pass --page or --page-id to choose where to move it."
        )
    page, index, section = copies[0]
    layout = page["layout"]
    assert isinstance(layout, list)
    layout.pop(index)
    target = max(0, min(position - 1, len(layout)))
    layout.insert(target, section)
    assign_sequence(layout, target)
    ensure_unique_sequences(layout)
    return {
        "section_id": section_id,
        "widget": section.get("widget"),
        "page_id": page["id"],
        "page_alias": page.get("alias"),
        "position": target + 1,
        "layout_count": len(layout),
    }


def set_theme(workspace: Workspace, new_settings: "dict[str, object]") -> "dict[str, object]":
    """Replace global settings entirely and report a top-level micro-diff."""
    diff = settings_diff(workspace.content.get("global_settings"), new_settings)
    workspace.content["global_settings"] = copy.deepcopy(new_settings)
    return {"global_settings_diff": diff}


# --- diff -----------------------------------------------------------------------------


def _section_map(page: "dict[str, object]") -> "dict[str, dict[str, object]]":
    return {
        str(section["id"]): section
        for section in page.get("layout") or []
        if isinstance(section, dict) and "id" in section
    }


def diff_summary(workspace: Workspace) -> "dict[str, object]":
    """Offline diff of the working copy against the base snapshot, by page and section."""
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    work_pages = {page["id"]: page for page in validated_pages(workspace.content)}

    pages_removed = [
        {"page_id": pid, "page_alias": base_pages[pid].get("alias"), "title": base_pages[pid]["title"]}
        for pid in base_pages
        if pid not in work_pages
    ]
    pages_added = [
        {"page_id": pid, "page_alias": work_pages[pid].get("alias"), "title": work_pages[pid]["title"]}
        for pid in work_pages
        if pid not in base_pages
    ]

    sections_added: "list[dict[str, object]]" = []
    sections_removed: "list[dict[str, object]]" = []
    sections_changed: "list[dict[str, object]]" = []
    seen_changed: "set[str]" = set()
    for pid, work_page in work_pages.items():
        base_page = base_pages.get(pid)
        # Sections of an added page are all additions: they must appear in the diff
        # and pass the push gates like any other new section.
        base_sections = _section_map(base_page) if base_page is not None else {}
        work_sections = _section_map(work_page)
        for sid in work_sections:
            entry = {
                "section_id": sid,
                "widget": work_sections[sid].get("widget"),
                "page_alias": work_page.get("alias"),
                "page_id": pid,
            }
            if sid not in base_sections:
                sections_added.append(entry)
            elif work_sections[sid] != base_sections[sid] and sid not in seen_changed:
                changed_entry = dict(entry)
                changed_entry["settings_diff"] = settings_diff(
                    base_sections[sid].get("settings"), work_sections[sid].get("settings")
                )
                order_changed = work_sections[sid].get("display_sequence") != base_sections[sid].get(
                    "display_sequence"
                )
                if order_changed:
                    changed_entry["order_changed"] = True
                sections_changed.append(changed_entry)
                seen_changed.add(sid)
        for sid in base_sections:
            if sid not in work_sections:
                sections_removed.append(
                    {
                        "section_id": sid,
                        "widget": base_sections[sid].get("widget"),
                        "page_alias": work_page.get("alias"),
                        "page_id": pid,
                    }
                )

    global_diff = settings_diff(workspace.base.get("global_settings"), workspace.content.get("global_settings"))
    return {
        "base_version_id": workspace.base_version_id,
        "pages_before": len(base_pages),
        "pages_after": len(work_pages),
        "pages_added": pages_added,
        "pages_removed": pages_removed,
        "sections_added": sections_added,
        "sections_removed": sections_removed,
        "sections_changed": sections_changed,
        "global_settings_changed": any(global_diff.values()),
        "global_settings_diff": global_diff,
        "sections_count": count_sections(workspace.content),
    }


def changed_or_added_sections(workspace: Workspace) -> "list[dict[str, object]]":
    """Every section of the working copy that differs from the base snapshot."""
    summary = diff_summary(workspace)
    result = []
    seen: "set[str]" = set()
    for entry in list(summary["sections_added"]) + list(summary["sections_changed"]):  # type: ignore[arg-type]
        sid = str(entry["section_id"])
        if sid in seen:
            continue
        seen.add(sid)
        copies = find_section_copies(workspace.content, sid)
        if copies:
            result.append(copies[0][2])
    return result
