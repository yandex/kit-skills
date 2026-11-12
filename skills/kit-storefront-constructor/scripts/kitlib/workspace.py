"""Local write workspace: pull snapshot, manifest, offline edits, and offline diff.

The workspace holds the full ConstructorContent so the agent never carries the ~660 KB
payload in its context. Every editing command mutates `work.json` only; the pristine base
snapshot keeps the pulled content until the next `content pull` or push rebase (templates
synced in are the one addition), and the manifest changes with it — or when a command
records the sections it copied (`copied_sections`).

Layout for a workspace at WORK:
    WORK                    — editable working copy (full ConstructorContent as read)
    WORK.base.json          — pristine base snapshot from the same pull
    WORK.manifest.json      — base_version_id, sha256 of the base snapshot, source, counts;
                              `copied_sections` once a command copies sections (see below)
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

from kitlib import pages as pages_engine
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
# Gap left between content sections when a block has to be renumbered, so the next
# insert finds a free integer instead of renumbering again.
CONTENT_SEQUENCE_STEP = 10
# Manifest key: sections a command copied from a section of the base under a new id
# (`page add --copy-layout-from default`), new id -> id of the base section. Their settings
# start as that section's server bytes, so what today's schema flags in them is the base
# section's old drift, not something the edit introduced.
COPIED_SECTIONS_KEY = "copied_sections"


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
    *,
    bff_url: "str | None" = None,
    schema_note: "str | None" = None,
) -> "dict[str, object]":
    """Persist a fresh pull: working copy, base snapshot, and manifest.

    The manifest records the storefront address the pull was made for (`bff_url`, None
    when it was not known) and the widget-schema build matched to it. Later workspace
    commands validate against the schema of that storefront, never against whatever
    single schema happens to sit in the cache.
    """
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
        "bff_url": bff_url,
        "schema_build_version": schema_build_version or None,
    }
    if schema_note:
        manifest["schema_note"] = schema_note
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
        self._manifest_dirty = False

    @property
    def base_version_id(self) -> int:
        return int(self.manifest["base_version_id"])  # validated in load()

    def save(self) -> None:
        """Persist the working copy; the base stays pristine.

        The manifest is rewritten only when a command recorded copied sections in it —
        its digest and version id are never touched here.
        """
        self.work_path.write_text(canonical_json(self.content), encoding="utf-8")
        if self._manifest_dirty:
            Path(str(self.work_path) + MANIFEST_SUFFIX).write_text(canonical_json(self.manifest), encoding="utf-8")
            self._manifest_dirty = False


def record_copied_sections(workspace: Workspace, origins: "dict[str, str]") -> None:
    """Remember which base section each new copy came from (saved with the working copy)."""
    if not origins:
        return
    manifest = dict(workspace.manifest)
    recorded = manifest.get(COPIED_SECTIONS_KEY)
    merged = dict(recorded) if isinstance(recorded, dict) else {}
    merged.update(origins)
    manifest[COPIED_SECTIONS_KEY] = merged
    workspace.manifest = manifest
    workspace._manifest_dirty = True


def copied_section_origin(workspace: Workspace, section_id: str) -> "dict[str, object] | None":
    """The base section a new section was copied from, or None.

    Only for a section the base does not have: once a push wrote the copy, it is a base
    section of its own.
    """
    recorded = workspace.manifest.get(COPIED_SECTIONS_KEY)
    if not isinstance(recorded, dict):
        return None
    origin_id = recorded.get(section_id)
    if not isinstance(origin_id, str):
        return None
    origin = None
    for page in validated_pages(workspace.base):
        for item in page.get("layout") or []:
            if not isinstance(item, dict):
                continue
            if item.get("id") == section_id:
                return None
            if origin is None and item.get("id") == origin_id:
                origin = item
    return origin


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
    if published:
        # The copies are in the new base now; drift there is theirs from here on.
        manifest.pop(COPIED_SECTIONS_KEY, None)
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
        raise UsageError(
            f"Section template {template_id!r} is not in this workspace's snapshot. A template "
            "created after 'content pull' is added with 'content templates-sync --work WORK', which "
            "keeps every edit in the workspace; pulling again would throw those edits away."
        )
    return matches[0]


# The fields that make a template what it is; timestamps and the create-time `version`
# record are bookkeeping and may be serialized differently by the two read routes.
TEMPLATE_SOURCE_FIELDS = ("title", "alias", "html", "css", "json_schema", "script")


def _template_field(template: "dict[str, object]", field: str) -> object:
    value = template.get(field)
    # The catalog always sends `script` (`""` without JS); an older snapshot may omit it.
    if field == "script" and value is None:
        return ""
    return value


def sync_section_templates(
    workspace: Workspace, server_templates: "list[dict[str, object]]"
) -> "dict[str, object]":
    """Add templates the snapshot does not know yet to both the working copy and the base.

    A template created after `content pull` cannot be placed until the snapshot holds it,
    and pulling again throws away every workspace edit. The write never carries templates
    — the server takes them from its own store — so adding a server record to the working
    copy and the base alike changes nothing the push sends, and the template gate (work
    equal to base) keeps holding. The base digest in the manifest is refreshed; its
    version id is not touched. A template the snapshot already knows is never replaced:
    a difference is reported, because it means the live template changed underneath.
    """
    work_templates = workspace.content.get("section_templates")
    base_templates = workspace.base.get("section_templates")
    if not isinstance(work_templates, list) or not isinstance(base_templates, list):
        raise UsageError("Workspace snapshot has no section_templates array; run 'content pull' again.")
    if work_templates != base_templates:
        raise UsageError(
            "section_templates in the working copy already differ from the base snapshot — the "
            "workspace was edited by hand. Run 'content pull' again."
        )
    known = {template.get("id"): template for template in base_templates if isinstance(template, dict)}
    added: "list[dict[str, object]]" = []
    changed: "list[dict[str, object]]" = []
    for record in server_templates:
        template_id = record.get("id")
        if template_id in known:
            differing = [
                field
                for field in TEMPLATE_SOURCE_FIELDS
                if _template_field(known[template_id], field) != _template_field(record, field)
            ]
            if differing:
                changed.append({"template_id": template_id, "title": record.get("title"), "fields": differing})
            continue
        snapshot_record = {key: copy.deepcopy(value) for key, value in record.items() if key != "version"}
        work_templates.append(copy.deepcopy(snapshot_record))
        base_templates.append(copy.deepcopy(snapshot_record))
        known[template_id] = snapshot_record
        added.append({"template_id": template_id, "title": record.get("title"), "alias": record.get("alias")})
    if added:
        serialized = canonical_json(workspace.base)
        Path(str(workspace.work_path) + BASE_SUFFIX).write_text(serialized, encoding="utf-8")
        manifest = dict(workspace.manifest)
        manifest["base_sha256"] = _sha256_text(serialized)
        manifest["templates_synced_at"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        workspace.manifest = manifest
        Path(str(workspace.work_path) + MANIFEST_SUFFIX).write_text(canonical_json(manifest), encoding="utf-8")
        workspace.save()
    report: "dict[str, object]" = {
        "templates_added": added,
        "templates_known": len(known) - len(added),
        "base_version_id": workspace.base_version_id,
    }
    if changed:
        report["templates_changed_on_server"] = changed
        report["changed_note"] = (
            "These templates differ on the server from the snapshot and were NOT replaced. A live "
            "template edit (made in the cabinet) shows on the storefront at once and in every "
            "version. Tell the user; 'content pull' takes the server state, discarding workspace edits."
        )
    return report


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


def _sequence(section: object) -> "int | None":
    value = section.get("display_sequence") if isinstance(section, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def is_chrome_anchor(section: object, content: "dict[str, object]") -> bool:
    """A header or footer slot copy: the content is renumbered around it, never through it.

    The server and `page add` put the header at 1 and the footer at 9999, but many pages
    carry other numbers: an announcement bar before the header pushes it to 2, and a page
    numbered 1..N keeps its footer right after the last section (13 on a page of twelve).
    The header keeps whatever number it has; the footer is lifted to 9999 when the content
    needs room under it (`_lift_footer`).
    """
    return isinstance(section, dict) and pages_engine.chrome_slot_of(section, content) is not None


def _run_around(layout: "list[object]", index: int, content: "dict[str, object]") -> "tuple[int, int]":
    """(start, end) of the block of regular sections that contains `index`."""
    start = index
    while start > 0 and not is_chrome_anchor(layout[start - 1], content):
        start -= 1
    end = index
    while end + 1 < len(layout) and not is_chrome_anchor(layout[end + 1], content):
        end += 1
    return start, end


def _lift_footer(layout: "list[object]", at: int, content: "dict[str, object]") -> "dict[str, object] | None":
    """Give the footer slot copy at layout[at] the sequence 9999 and the last place in the list.

    The storefront renders the footer after the page content whatever its number (it
    groups a page's sections into header area, content and footer), and the number is
    stored per page copy, so nothing visible changes and no other page is touched; the
    content just gets the numbers it needs. Returns what changed, or None when layout[at]
    is not a footer copy below 9999.
    """
    if at >= len(layout):
        return None
    footer = layout[at]
    if not isinstance(footer, dict) or pages_engine.chrome_slot_of(footer, content) != "footer":
        return None
    before = _sequence(footer)
    others = [_sequence(item) for position, item in enumerate(layout) if position != at]
    target = max([pages_engine.FOOTER_SEQUENCE] + [value + 1 for value in others if value is not None])
    if before is not None and before >= target:
        return None
    layout.pop(at)
    footer["display_sequence"] = target
    layout.append(footer)
    return {"slot": "footer", "section_id": footer.get("id"), "before": before, "after": target}


def _renumber_run(layout: "list[object]", start: int, end: int, content: "dict[str, object]") -> None:
    """Renumber layout[start..end] strictly between the chrome slots around it, in steps of ten.

    Everything outside the block — the header, the footer, whatever the server put before
    the header (an announcement bar at 0) — keeps its sequence. A block that does not fit
    below the slot after it is refused rather than pushed through it; the footer never
    gets here too low, `_renumber_block` lifts it first.
    """
    count = end - start + 1
    floor = _sequence(layout[start - 1]) if start > 0 else None
    ceiling_item = layout[end + 1] if end + 1 < len(layout) else None
    ceiling = _sequence(ceiling_item)
    if floor is None and ceiling is None:
        first = 1  # a page without header and footer slots numbers from 1, like the server
    else:
        first = (floor if floor is not None else -1) + 1
    step = CONTENT_SEQUENCE_STEP
    if ceiling is not None:
        if first + count - 1 >= ceiling:
            slot = pages_engine.chrome_slot_of(ceiling_item, content) if isinstance(ceiling_item, dict) else None
            # The block holds the section being placed and the ones already there.
            already = f"; {count - 1} section(s) already stand there" if count > 1 else ""
            if slot == "header":
                raise UsageError(
                    f"There is no free sequence for this section before the header slot, which keeps "
                    f"its sequence {ceiling}{already}. Put the section after the header (a higher "
                    "--position): the storefront renders the header area above the content whatever "
                    "the order."
                )
            raise UsageError(
                f"There is no free sequence for this section between {floor} and the "
                f"{slot or 'next'} slot at {ceiling}{already}."
            )
        if count > 1:
            step = max(1, min(CONTENT_SEQUENCE_STEP, (ceiling - 1 - first) // (count - 1)))
    for offset, section in enumerate(layout[start : end + 1]):
        if isinstance(section, dict):
            section["display_sequence"] = first + offset * step


def _renumber_block(layout: "list[object]", index: int, content: "dict[str, object]") -> "dict[str, object] | None":
    """Renumber the block of regular sections around layout[index]; report a footer lift.

    The footer slot under the block is lifted to 9999 first when it sits lower: on a page
    numbered 1..N it leaves no room at all, and a lifted footer gives the block its steps of
    ten, so the next insert finds a free number instead of renumbering again.
    """
    start, end = _run_around(layout, index, content)
    lifted = _lift_footer(layout, end + 1, content)
    if lifted is not None:
        start, end = _run_around(layout, index, content)
    _renumber_run(layout, start, end, content)
    return lifted


def assign_sequence(
    layout: "list[object]", index: int, content: "dict[str, object]"
) -> "dict[str, object] | None":
    """Give layout[index] a display_sequence between its neighbours; report a footer lift.

    Prefers an unused integer in the neighbour gap so untouched sections keep their
    server-assigned sequence (smallest possible write diff). When the gap is exhausted
    only the block of regular sections between the header and footer slots is
    renumbered; the header keeps its sequence, the footer at most moves up to 9999.
    """
    section = layout[index]
    assert isinstance(section, dict)
    low = _sequence(layout[index - 1]) if index > 0 else None
    high = _sequence(layout[index + 1]) if index + 1 < len(layout) else None
    if low is None and high is None:
        section["display_sequence"] = 1
        return None
    if high is None:
        assert low is not None
        section["display_sequence"] = low + 1
        return None
    if low is None:
        if high > 0:
            section["display_sequence"] = high - 1
            return None
    elif high - low > 1:
        section["display_sequence"] = low + 1
        return None
    return _renumber_block(layout, index, content)


def ensure_unique_sequences(
    layout: "list[object]", content: "dict[str, object]"
) -> "list[dict[str, object]]":
    """Renumber a block whose display_sequence values collide (unordered server layout edge case).

    Only blocks of regular sections are renumbered; a collision between two chrome slots is
    the server's own data and is carried through as read. Returns the footer lifts, if any.
    """
    counts: "dict[object, int]" = {}
    for section in layout:
        if isinstance(section, dict):
            key = section.get("display_sequence")
            counts[key] = counts.get(key, 0) + 1
    lifts: "list[dict[str, object]]" = []
    index = 0
    while index < len(layout):
        if is_chrome_anchor(layout[index], content):
            index += 1
            continue
        start, end = _run_around(layout, index, content)
        if any(
            isinstance(section, dict) and counts.get(section.get("display_sequence"), 0) > 1
            for section in layout[start : end + 1]
        ):
            lifted = _renumber_block(layout, index, content)
            if lifted is not None:
                lifts.append(lifted)
            start, end = _run_around(layout, index, content)
        index = end + 1
    return lifts


def _content_insert_limit(layout: "list[object]", content: "dict[str, object]") -> int:
    """The last index a section may take: right before a trailing footer slot."""
    limit = len(layout)
    while limit > 0:
        last = layout[limit - 1]
        if not isinstance(last, dict) or pages_engine.chrome_slot_of(last, content) != "footer":
            break
        limit -= 1
    return limit


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


def slot_switch_warning(slot: str, before: object, after: object) -> str:
    """What switching the header or footer slot means for the store, said before the push."""
    return (
        f"STORE-WIDE {slot.upper()}: the {slot} of EVERY page changes from {before} to {after}, "
        "including pages the server adds later. A custom header does not get what the built-in one "
        "has: the content offset under a transparent sticky header and the placement of the cookie "
        "banner; a store design theme applied later replaces only built-in headers and footers. This "
        "switch has not been tried against a live store by this skill: prefer 'content push "
        "--no-publish' and have the user look at the proposal (desktop and phone) before publishing. "
        "The way back is the same command with the other widget."
    )


def check_slot_switch(
    workspace: Workspace,
    section_id: str,
    widget: str,
    template_id: "str | None",
) -> "tuple[list[tuple[dict[str, object], int, dict[str, object]]], str]":
    """Refuse a widget switch that is not a header or footer slot change; (copies, slot).

    Offline and before anything else: a section that is not a slot, or a target the slot
    cannot hold, is refused before its settings are read, validated or a widget version is
    asked from the storefront.
    """
    copies = require_section_copies(workspace.content, section_id)
    slot = pages_engine.chrome_slot_of(copies[0][2], workspace.base)
    if slot is None:
        raise UsageError(
            f"Section {section_id!r} is not a header or footer slot. Only the slot changes widget here "
            "(built-in ↔ custom template); a regular section is replaced by adding a new one."
        )
    builtin = pages_engine.CHROME_WIDGET_BY_SLOT[slot]
    if widget == MYSTIQUE_WIDGET:
        if not template_id:
            raise UsageError(f"Switching the {slot} slot to {MYSTIQUE_WIDGET} needs --template-id.")
        section_template(workspace.base, template_id)
        if pages_engine.template_chrome_slot(workspace.base, template_id) != slot:
            raise UsageError(
                f"Template {template_id} does not carry alias {builtin}: the server treats a template as "
                f"the store {slot} only by that alias. Create it with 'templates create --chrome-slot {slot}'."
            )
    elif widget == builtin:
        if template_id:
            raise UsageError(f"The built-in {builtin} takes no --template-id.")
    else:
        raise UsageError(
            f"The {slot} slot holds either the built-in {builtin} or a {MYSTIQUE_WIDGET} template with "
            f"alias {builtin}; {widget!r} is neither."
        )
    return copies, slot


def switch_slot_widget(
    workspace: Workspace,
    section_id: str,
    widget: str,
    template_id: "str | None",
    new_settings: "dict[str, object]",
) -> "dict[str, object]":
    """Put a custom template into the header or footer slot, or the built-in widget back.

    The slot is one composite section — the same id on every page — so every copy
    switches together: widget, template and settings, which the server keeps once per id.
    A template must carry the slot's own alias (`YandexKit.Header` / `YandexKit.Footer`),
    the alias the server goes by; the built-in widget must be the slot's own. Settings
    are replaced, not merged, and `settings.version` follows the new widget — the
    version-preserving rule of `set_section_settings` does not apply across widgets.
    """
    copies, slot = check_slot_switch(workspace, section_id, widget, template_id)
    current = copies[0][2]
    before_widget = current.get("widget")
    before_template = current.get("template_id") if before_widget == MYSTIQUE_WIDGET else None
    before = f"template {before_template}" if before_template else before_widget
    for _page, _index, section in copies:
        section["widget"] = widget
        section["settings"] = copy.deepcopy(new_settings)
        if widget == MYSTIQUE_WIDGET:
            section["template_id"] = template_id
        else:
            section.pop("template_id", None)
    after = f"template {template_id}" if widget == MYSTIQUE_WIDGET else widget
    return {
        "section_id": section_id,
        "slot": slot,
        "widget_changed": {"from": before_widget, "to": widget},
        "template_changed": {"from": before_template, "to": template_id if widget == MYSTIQUE_WIDGET else None},
        "pages_updated": [
            {"page_id": page["id"], "page_alias": page.get("alias"), "section_type": section.get("section_type")}
            for page, _index, section in copies
        ],
        "store_wide_warning": slot_switch_warning(slot, before, after),
    }


def _place(
    layout: "list[object]", index: int, section: "dict[str, object]", content: "dict[str, object]"
) -> "dict[str, object] | None":
    """Insert a section and number it; on refusal the layout is left as it was.

    Returns the footer lift when the footer slot copy had to move up to 9999 for room.
    """
    before = copy.deepcopy(layout)
    layout.insert(index, section)
    try:
        lifts = [assign_sequence(layout, index, content)]
        lifts.extend(ensure_unique_sequences(layout, content))
    except UsageError:
        layout[:] = before
        raise
    return next((lift for lift in lifts if lift is not None), None)


def _report_footer_lift(report: "dict[str, object]", lift: "dict[str, object] | None") -> "dict[str, object]":
    """Name a lifted footer in the command report, so the change is not a surprise in the diff."""
    if lift is not None:
        report["footer_renumbered"] = dict(
            lift,
            note=(
                "The footer slot on this page sat right after the content and left no free "
                "number, so its copy here moved up to sequence 9999. The storefront shows the "
                "footer below the content whatever its number; nothing visible changes and "
                "other pages keep their own numbers."
            ),
        )
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
        # A new section is visible; `section status` hides it afterwards when asked.
        "status": "published",
    }
    if template_id is not None:
        section["template_id"] = template_id
    # The end of the page is the end of its content: right before the footer slot, never
    # after it — an appended section would otherwise render below the store footer.
    limit = _content_insert_limit(layout, workspace.content)
    index = limit if position is None else max(0, min(position - 1, limit))
    lift = _place(layout, index, section, workspace.content)
    return _report_footer_lift(
        {
            "section_id": section["id"],
            "widget": widget,
            "page_id": page["id"],
            "page_alias": page.get("alias"),
            "position": index + 1,
            "layout_count": len(layout),
        },
        lift,
    )


def _copies_on_page(
    workspace: Workspace, section_id: str, alias: "str | None", page_id: "str | None"
) -> "list[tuple[dict[str, object], int, dict[str, object]]]":
    """Every copy of a section id, or only the ones on the page named by alias / id."""
    copies = require_section_copies(workspace.content, section_id)
    if alias is None and page_id is None:
        return copies
    page = select_page(workspace.content, alias, page_id)
    selected = [(p, i, s) for p, i, s in copies if p["id"] == page["id"]]
    if not selected:
        raise UsageError(f"Section {section_id!r} is not on the selected page.")
    return selected


def _repair_relation_types(copies: "list[tuple[dict[str, object], int, dict[str, object]]]") -> None:
    """Keep the server's composite rule after a copy left: one parent, the rest children.

    The server refuses a write where a section id has children but no parent, so when the
    parent copy goes, the first remaining copy takes its place (its widget, template and
    settings already match — the composite gate keeps them equal). A single remaining
    copy is an ordinary section.
    """
    if not copies:
        return
    if len(copies) == 1:
        copies[0][2]["section_type"] = "usual"
        return
    if any(section.get("section_type") == "parent" for _page, _index, section in copies):
        return
    copies[0][2]["section_type"] = "parent"
    for _page, _index, section in copies[1:]:
        section["section_type"] = "child"


def remove_section(
    workspace: Workspace,
    section_id: str,
    alias: "str | None" = None,
    page_id: "str | None" = None,
) -> "dict[str, object]":
    """Remove every copy of one section id, or only its copy on one page.

    Without a page the composite copies disappear together. With one, the copy on that
    page goes and the others stay: a block shared by several pages (an FAQ copied from the
    main page onto the catalog) can be taken off the page where it only repeats. The
    header and footer slots are refused per page — they are one section on every page.
    """
    copies = _copies_on_page(workspace, section_id, alias, page_id)
    per_page = alias is not None or page_id is not None
    first = copies[0][2]
    slot = pages_engine.chrome_slot_of(first, workspace.content)
    if per_page and slot is not None:
        raise UsageError(
            f"Section {section_id!r} is the store {slot} slot: one section on every page. It is not "
            "removed from a single page here — the slot exists to be the same everywhere, and the "
            "server puts a missing footer back from the main page anyway."
        )
    pages_affected = []
    for page, _index, _section in copies:
        layout = page["layout"]
        assert isinstance(layout, list)
        layout[:] = [item for item in layout if not (isinstance(item, dict) and item.get("id") == section_id)]
        pages_affected.append({"page_id": page["id"], "page_alias": page.get("alias")})
    remaining = find_section_copies(workspace.content, section_id)
    _repair_relation_types(remaining)
    report: "dict[str, object]" = {
        "section_id": section_id,
        "widget": first.get("widget"),
        "removed_copies": len(copies),
        "pages_affected": pages_affected,
    }
    if per_page:
        report["remaining_copies"] = [
            {"page_id": page["id"], "page_alias": page.get("alias"), "section_type": section.get("section_type")}
            for page, _index, section in remaining
        ]
    return report


def set_section_status(
    workspace: Workspace,
    section_id: str,
    status: str,
    alias: "str | None" = None,
    page_id: "str | None" = None,
) -> "dict[str, object]":
    """Hide or show a section: every copy, or the copy on one page.

    Status is stored per copy, so a block shared by several pages can be hidden on one of
    them. `hidden` is accepted by the server only for the widgets in
    HIDEABLE_SECTION_WIDGETS; anything else is refused here, where the server would
    refuse the whole write with 400.
    """
    if status not in pages_engine.SECTION_STATUSES:
        raise UsageError(f"Section status must be one of: {', '.join(pages_engine.SECTION_STATUSES)}.")
    copies = _copies_on_page(workspace, section_id, alias, page_id)
    widget = copies[0][2].get("widget")
    if status == "hidden" and widget not in pages_engine.HIDEABLE_SECTION_WIDGETS:
        allowed = ", ".join(sorted(pages_engine.HIDEABLE_SECTION_WIDGETS))
        slot = pages_engine.chrome_slot_of(copies[0][2], workspace.content)
        if slot is not None:
            # Neither way out of the generic refusal exists for the slot: it is not removed
            # from one page either.
            raise UsageError(
                f"The built-in {widget} cannot be hidden: the server answers the whole write with 400 "
                f"(«Секция {widget!r} не может быть скрыта»). It is the store {slot} slot, one section on "
                "every page, so it is not removed from a single page either. What it shows changes with "
                f"'section set --id {section_id}' (its settings), and 'section set --widget "
                f"{MYSTIQUE_WIDGET} --template-id ...' puts a custom {slot} template into the slot; "
                "that one can be hidden, on every page or on one."
            )
        raise UsageError(
            f"{widget} cannot be hidden: the server answers the whole write with 400 («Секция "
            f"{widget!r} не может быть скрыта»). Hideable widgets: {allowed}. To take it off a page, "
            "remove it there: section remove --id ... --page ALIAS."
        )
    changed = []
    for page, _index, section in copies:
        before = section.get("status")
        if before == status:
            continue
        section["status"] = status
        changed.append({"page_id": page["id"], "page_alias": page.get("alias"), "before": before, "after": status})
    report: "dict[str, object]" = {
        "section_id": section_id,
        "widget": widget,
        "status": status,
        "copies_changed": changed,
        "copies_unchanged": len(copies) - len(changed),
    }
    slot = pages_engine.chrome_slot_of(copies[0][2], workspace.content)
    if slot is not None and changed and status == "hidden":
        scope = "on every page" if alias is None and page_id is None else "on this page"
        report["store_wide_warning"] = (
            f"This is the store {slot} slot: the storefront shows no {slot} {scope} after the push."
        )
    return report


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
    slot = pages_engine.chrome_slot_of(section, workspace.content)
    if slot is not None:
        area = "above" if slot == "header" else "below"
        raise UsageError(
            f"Section {section_id!r} is the store {slot} slot: the storefront renders it {area} the "
            "page content whatever its place in the layout, so it is not moved. Move the content "
            "sections instead."
        )
    layout = page["layout"]
    assert isinstance(layout, list)
    before = copy.deepcopy(layout)
    layout.pop(index)
    target = max(0, min(position - 1, _content_insert_limit(layout, workspace.content)))
    try:
        lift = _place(layout, target, section, workspace.content)
    except UsageError:
        layout[:] = before
        raise
    return _report_footer_lift(
        {
            "section_id": section_id,
            "widget": section.get("widget"),
            "page_id": page["id"],
            "page_alias": page.get("alias"),
            "position": target + 1,
            "layout_count": len(layout),
        },
        lift,
    )


def set_theme(workspace: Workspace, new_settings: "dict[str, object]") -> "dict[str, object]":
    """Replace global settings entirely and report a top-level micro-diff."""
    diff = settings_diff(workspace.content.get("global_settings"), new_settings)
    workspace.content["global_settings"] = copy.deepcopy(new_settings)
    return {"global_settings_diff": diff}


# --- diff -----------------------------------------------------------------------------


def _without_status(section: "dict[str, object]") -> "dict[str, object]":
    return {key: value for key, value in section.items() if key != "status"}


def _section_map(page: "dict[str, object]") -> "dict[str, dict[str, object]]":
    return {
        str(section["id"]): section
        for section in page.get("layout") or []
        if isinstance(section, dict) and "id" in section
    }


# The human-owned page fields a write can change on a page that stays. `exact` follows
# the pattern, and `alias` / `variant_type` are immutable, so they are not listed here.
PAGE_EDITABLE_FIELDS = ("title", "pattern", "status", "meta")


def pages_changed(workspace: Workspace) -> "list[dict[str, object]]":
    """Pages present in both snapshots whose title, pattern, status or SEO meta differ.

    Without this the diff and the push plan list only added and removed pages, and a
    write that retitles, hides, moves or re-SEOs pages looks empty to the person asked
    to confirm it.
    """
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    changed: "list[dict[str, object]]" = []
    for page in validated_pages(workspace.content):
        base_page = base_pages.get(page["id"])
        if base_page is None:
            continue
        fields = {
            field: {"before": copy.deepcopy(base_page.get(field)), "after": copy.deepcopy(page.get(field))}
            for field in PAGE_EDITABLE_FIELDS
            if base_page.get(field) != page.get(field)
        }
        if fields:
            changed.append(
                {
                    "page_id": page["id"],
                    "alias": page.get("alias"),
                    "pattern": page.get("pattern"),
                    "fields": fields,
                }
            )
    return changed


def diff_summary(workspace: Workspace) -> "dict[str, object]":
    """Offline diff of the working copy against the base snapshot, by page and section."""
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    work_pages = {page["id"]: page for page in validated_pages(workspace.content)}

    pages_removed = [
        {
            "page_id": pid,
            "page_alias": base_pages[pid].get("alias"),
            "variant_type": base_pages[pid].get("variant_type"),
            "title": base_pages[pid]["title"],
        }
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
    section_status_changed: "list[dict[str, object]]" = []
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
                continue
            work_section, base_section = work_sections[sid], base_sections[sid]
            # Status lives on each page copy, so it is reported per copy, not once per id —
            # in section_status_changed only: a copy whose status alone changed is not a
            # changed section with an empty settings diff.
            status_changed = work_section.get("status") != base_section.get("status")
            if status_changed:
                section_status_changed.append(
                    dict(entry, before=base_section.get("status"), after=work_section.get("status"))
                )
            if _without_status(work_section) != _without_status(base_section) and sid not in seen_changed:
                changed_entry = dict(entry)
                if status_changed:
                    changed_entry["status_changed"] = {
                        "before": base_section.get("status"),
                        "after": work_section.get("status"),
                    }
                changed_entry["settings_diff"] = settings_diff(
                    base_section.get("settings"), work_section.get("settings")
                )
                if work_section.get("display_sequence") != base_section.get("display_sequence"):
                    changed_entry["order_changed"] = True
                for field, key in (
                    ("widget", "widget_changed"),
                    ("template_id", "template_changed"),
                    ("section_type", "section_type_changed"),
                ):
                    if work_section.get(field) != base_section.get(field):
                        changed_entry[key] = {"before": base_section.get(field), "after": work_section.get(field)}
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
        "pages_changed": pages_changed(workspace),
        "sections_added": sections_added,
        "sections_removed": sections_removed,
        "sections_changed": sections_changed,
        "section_status_changed": section_status_changed,
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
