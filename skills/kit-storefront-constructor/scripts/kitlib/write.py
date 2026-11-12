"""Write core: full-replace PUT body assembly and local integrity gates.

The only write transport is ``PUT /constructor/content`` — a full replace of every page.
The price of an assembly mistake is the whole storefront, so every gate below runs
locally, before any confirmation prompt, token load, or network access.
"""

from __future__ import annotations

import copy

from kitlib import pages as pages_engine
from kitlib import schema as schema_engine
from kitlib import workspace as ws
from kitlib.common import UsageError, require_mapping, validated_pages

# Read-shape page fields the PUT request schema does not accept (PageWithUsagesCount
# extras on top of Page in api/experimental/openapi.yaml).
READ_ONLY_PAGE_FIELDS = ("categories_count", "collections_count", "product_cards_count")
DEFAULT_COMMIT_MESSAGE = "Обновление контента конструктора через API"
MYSTIQUE_WIDGET = "YandexKit.Mystique"
# What the server stores once per section id and reads from the parent copy.
COMPOSITE_SHARED_FIELDS = ("widget", "template_id", "settings")


def page_for_put(page: "dict[str, object]") -> "dict[str, object]":
    """Convert one read-shape page into the accepted write shape.

    The pattern goes out as it was read, aliased pages included. A newer server writes the
    alias's canonical pattern whatever is sent; an older one refuses anything but its own
    table entry — and the pattern it gave on read is that entry. Sending the canonical
    pattern of the client's table instead would break the older one.
    """
    converted = {key: copy.deepcopy(value) for key, value in page.items() if key not in READ_ONLY_PAGE_FIELDS}
    return converted


def build_put_body(
    workspace: ws.Workspace,
    commit_message: str,
    activate: bool = True,
) -> "dict[str, object]":
    """Assemble the exact UpdateConstructorContentRequest body from the workspace.

    ``activate`` is sent only when False: the publish path keeps today's wire shape,
    and a server that predates the field would silently publish anyway — the caller
    must verify ``active_version_id`` in the response, not trust the request.
    """
    body: "dict[str, object]" = {
        "pages": [page_for_put(page) for page in validated_pages(workspace.content)],
        "global_settings": copy.deepcopy(workspace.content["global_settings"]),
        "base_version_id": workspace.base_version_id,
        "commit_message": commit_message,
    }
    if not activate:
        body["activate"] = False
    return body


def _is_protected_page(page: "dict[str, object]") -> bool:
    """A default-variant page with an alias can never be dropped: the server 400s the whole transaction."""
    alias = page.get("alias")
    return bool(alias) and page.get("variant_type") == "default"


def _neutralize_partials_refs(node: object) -> object:
    """Replace `#/definitions/partials/...` refs with a permissive schema.

    Fragment partials are rendered server-side from section_template_fragments; there is
    no local schema to check against, and failing every fragment-using template closed
    would make most live custom sections uneditable.
    """
    if isinstance(node, dict):
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/definitions/partials/"):
            return {}
        return {key: _neutralize_partials_refs(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_neutralize_partials_refs(item) for item in node]
    return node


def _template_schema(
    workspace: ws.Workspace, template_id: str
) -> "tuple[dict[str, object] | None, str | None]":
    """A template's json_schema decoded, partials refs neutralized; or None and why not."""
    import json

    try:
        template = ws.section_template(workspace.base, template_id)
    except UsageError as error:
        return None, str(error)
    raw_schema = template.get("json_schema")
    if not isinstance(raw_schema, str):
        return None, f"template {template_id} json_schema must be a string"
    try:
        decoded = json.loads(raw_schema)
    except json.JSONDecodeError as error:
        return None, f"template {template_id} json_schema is invalid JSON: {error.msg}"
    if not isinstance(decoded, dict):
        return None, f"template {template_id} json_schema must decode to an object"
    return _neutralize_partials_refs(decoded), None  # type: ignore[return-value]


def _external_definitions(schema: "dict[str, object]") -> "list[str]":
    """Definitions a template schema refers to and does not define itself, sorted.

    Those are the storefront editor models (ImageModel, LinkSource, Palette, …): they
    resolve only against the BFF widget schema of the store.
    """
    own = schema.get("definitions")
    own = own if isinstance(own, dict) else {}
    found: "set[str]" = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/definitions/"):
                name = reference[len("#/definitions/") :]
                if name not in own:
                    found.add(name)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(schema)
    return sorted(found)


def _mystique_errors(
    section: "dict[str, object]",
    workspace: ws.Workspace,
    schema_document: "dict[str, object] | None",
    label: str,
) -> "tuple[list[str], list[str]]":
    """A custom section's validation errors, and the storefront editor models it lacked.

    The second value is non-empty only when there is no widget schema and these settings
    reach a definition the template takes from it: then nothing can be said about them
    until `schema fetch` for the store. Settings that never reach one validate fully
    against the template alone — the validator resolves a `$ref` only where it walks.
    """
    template_id = section.get("template_id")
    if not isinstance(template_id, str) or not template_id:
        return [f"{label}: a {MYSTIQUE_WIDGET} section requires template_id"], []
    decoded, problem = _template_schema(workspace, template_id)
    if decoded is None:
        return [f"{label}: {problem}"], []
    # Live template schemas resolve editor models (ImageModel, LinkSource, TextArea, …)
    # against the BFF definitions, and fragment refs against `#/definitions/partials/…`,
    # which exists nowhere as a schema — fragments carry their own schema server-side.
    # Combined resolution: own definitions win, BFF definitions fill the editor models,
    # and partials refs are treated as unconstrained rather than failing every template.
    template_definitions = decoded.get("definitions")
    template_definitions = template_definitions if isinstance(template_definitions, dict) else {}
    combined_definitions: "dict[str, object]" = {}
    if schema_document is not None:
        bff_definitions = schema_document.get("definitions")
        if isinstance(bff_definitions, dict):
            combined_definitions.update(bff_definitions)
    combined_definitions.update(template_definitions)
    settings = section.get("settings")
    try:
        messages = schema_engine.validate_instance(
            settings if settings is not None else {}, decoded, combined_definitions
        )
    except schema_engine.MissingDefinition as error:
        external = _external_definitions(decoded)
        if schema_document is None and error.name in external:
            return [
                f"{label}: template {template_id} takes editor models ({', '.join(external[:5])}) from "
                "the storefront's widget schema, and no widget schema is known for this workspace's "
                "storefront; pass the store's own address with --bff-url (or set "
                "KIT_BFF_BASE_URL) and run 'schema fetch' first"
            ], external
        build = schema_document.get("buildVersion") if schema_document is not None else None
        where = f"the cached widget schema (build {build})" if schema_document is not None else "any schema"
        return [
            f"{label}: template {template_id} refers to {error.name}, which neither the template nor "
            f"{where} defines; refresh the schema with 'schema fetch --force', and if it stays, "
            "the template itself is broken"
        ], []
    except UsageError as error:
        return [f"{label}: template {template_id}: {error}"], []
    return [f"{label}: {message}" for message in messages], []


def bff_models_needed(section: "dict[str, object]", workspace: ws.Workspace) -> "list[str]":
    """The editor models a custom section's settings need from a widget schema not at hand.

    Empty when the settings validate against the template alone, or the section is not a
    custom one.
    """
    if section.get("widget") != MYSTIQUE_WIDGET:
        return []
    return _mystique_errors(section, workspace, None, f"section {section.get('id')} ({MYSTIQUE_WIDGET})")[1]


def _validate_section_settings(
    section: "dict[str, object]",
    workspace: ws.Workspace,
    schema_document: "dict[str, object] | None",
) -> "list[str]":
    """Validate one changed section's settings against its authoritative schema.

    A schema problem — a template referring to a definition nobody has, a broken cached
    schema — comes back as an error of this section, never as an exception out of a gate.
    """
    widget = section.get("widget")
    settings = section.get("settings")
    label = f"section {section.get('id')} ({widget})"
    if widget == MYSTIQUE_WIDGET:
        return _mystique_errors(section, workspace, schema_document, label)[0]
    if schema_document is None:
        return [
            f"{label}: a built-in widget cannot be checked without the cached widget schema; "
            "run 'schema fetch' first"
        ]
    try:
        node = schema_engine.widget_schema(schema_document, str(widget))
    except UsageError as error:
        return [f"{label}: {error}"]
    definitions = schema_document["definitions"]
    assert isinstance(definitions, dict)
    try:
        messages = schema_engine.validate_instance(settings if settings is not None else {}, node, definitions)
    except UsageError as error:
        return [f"{label}: {error}"]
    return [f"{label}: {message}" for message in messages]


def introduced_errors(
    section: "dict[str, object]",
    workspace: ws.Workspace,
    schema_document: "dict[str, object] | None",
    baseline: "tuple[str, ...]",
) -> "list[str]":
    """Validation errors the edit introduces, ignoring drift already present in the base.

    Live storefront data predates schema validation on the backend, so server-origin
    settings may not validate against today's schema (for example ``width: null`` where
    the schema says number). Blocking on pre-existing drift would make such sections
    uneditable; only errors absent from the base settings block the write.
    """
    return [error for error in _validate_section_settings(section, workspace, schema_document) if error not in baseline]


def _baseline_errors(
    reference: "dict[str, object] | None",
    section: "dict[str, object]",
    workspace: ws.Workspace,
    schema_document: "dict[str, object] | None",
) -> "tuple[str, ...]":
    """What today's schema already flags in the server section an edit started from.

    ``reference`` is the base section with the same id, or — for a copy made under a new id
    (`page add --copy-layout-from default`) — the base section it was copied from; judged
    under the edited section's id, so the messages compare. Empty when there is nothing to
    start from or no schema: the fail-closed "no cached schema" error never cancels itself.
    """
    if reference is None or schema_document is None:
        return ()
    return tuple(_validate_section_settings(dict(reference, id=section.get("id")), workspace, schema_document))


def inherited_drift(
    workspace: ws.Workspace,
    schema_document: "dict[str, object] | None",
    section_ids: "list[str]",
) -> "list[dict[str, object]]":
    """Copied sections whose settings today's schema flags, with the first findings each.

    The copies carry the settings of the section they came from byte for byte; the server
    stores them as they are and that section already renders with them.
    """
    if schema_document is None:
        return []
    found: "list[dict[str, object]]" = []
    for section_id in section_ids:
        copies = ws.find_section_copies(workspace.content, section_id)
        if not copies:
            continue
        section = copies[0][2]
        label = f"section {section_id} ({section.get('widget')}): "
        errors = [
            error[len(label):] if error.startswith(label) else error
            for error in _validate_section_settings(section, workspace, schema_document)
        ]
        if errors:
            found.append({"section_id": section_id, "widget": section.get("widget"), "findings": errors[:3]})
    return found


def check_gates(
    workspace: ws.Workspace,
    schema_document: "dict[str, object] | None",
    allow_page_removal: bool,
) -> "list[str]":
    """Run every local integrity gate; an empty list means the body may be assembled.

    Sections identical to the base snapshot need no re-checking — they came from the
    server byte-for-byte. Every added or changed section is gated: widget known (BFF
    schema, or Mystique with a catalogued template), settings valid, composite copies
    in sync.
    """
    errors: "list[str]" = []
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    work_pages = {page["id"]: page for page in validated_pages(workspace.content)}

    # Gate: template immutability. The write transport does not carry section_templates —
    # the server keeps the existing ones for every version, so a template edit in the
    # workspace can neither be written nor previewed through push. `content templates-sync`
    # adds new templates to the working copy and the base alike and no other command touches
    # them, so a difference here means the workspace was hand-edited.
    if workspace.content.get("section_templates") != workspace.base.get("section_templates"):
        errors.append(
            "section_templates differ from the base snapshot; PUT /constructor/content does not "
            "transmit templates, so this change can neither be written nor previewed. Templates "
            "live outside content versions: to change a custom section's markup, create a NEW "
            "template with 'templates create', add it to the snapshot with 'content templates-sync' "
            "(or 'templates create --work'), and move the "
            "section onto it with 'section set --template-id'. Run 'content pull' to restore "
            "this workspace."
        )

    # Gate: page loss. Full-replace drops every page missing from the body.
    for pid, base_page in base_pages.items():
        if pid in work_pages:
            continue
        if _is_protected_page(base_page):
            errors.append(
                f"page {base_page.get('alias')!r} ({pid}) is a default page with an alias and is "
                "missing from the workspace; the server would reject the whole write with 400. "
                "Run 'content pull' again — this workspace is corrupted."
            )
        elif not allow_page_removal:
            errors.append(
                f"page {base_page.get('title')!r} ({pid}) is missing from the workspace; "
                "a full-replace write would delete it silently. Pass --allow-page-removal "
                "only if this deletion is intended."
            )

    # Gate: alias immutability.
    for pid, work_page in work_pages.items():
        base_page = base_pages.get(pid)
        if base_page is not None and work_page.get("alias") != base_page.get("alias"):
            errors.append(
                f"page {pid}: alias changed from {base_page.get('alias')!r} to "
                f"{work_page.get('alias')!r}; changing an alias is forbidden by the API."
            )

    # Gates: page-level server refusals and client policy (patterns, aliases, variants,
    # statuses, SEO meta) — added or changed pages only; each rule is labelled in kitlib/pages.py.
    errors.extend(pages_engine.page_gate_errors(workspace))

    # Gates: widget existence, settings validity, composite sync — changed sections only.
    # Order-only changes (display_sequence after an insert/move renumber) skip settings
    # validation deliberately: those settings are server-origin bytes, and the backend
    # never validated them against today's schema, so re-checking them here would block
    # a legitimate move because of pre-existing drift the client did not introduce.
    base_sections: "dict[str, dict]" = {}
    for page in validated_pages(workspace.base):
        for item in page.get("layout") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                base_sections.setdefault(item["id"], item)
    changed = ws.changed_or_added_sections(workspace)
    for section in changed:
        sid_key = str(section.get("id"))
        base_section = base_sections.get(sid_key)
        is_new = base_section is None
        # A copy under a new id is judged against the section it was copied from: the
        # settings it starts with are that section's server bytes, drift included.
        origin = ws.copied_section_origin(workspace, sid_key) if is_new else None
        # A rebind can leave the settings byte-identical and still invalidate them: the
        # schema that judges them belongs to the template, so a moved section is as much
        # a candidate for re-checking as an edited one.
        template_changed = (
            not is_new and section.get("template_id") != base_section.get("template_id")
        )
        settings_changed = (
            is_new or template_changed or section.get("settings") != base_section.get("settings")
        )
        if settings_changed:
            # Built from the base section as the server had it — template included — because
            # judging the old settings under the *new* template would whitelist exactly the
            # breakage a rebind is meant to surface.
            baseline = _baseline_errors(base_section if not is_new else origin, section, workspace, schema_document)
            errors.extend(introduced_errors(section, workspace, schema_document, baseline))
        sid = str(section.get("id"))
        # A header or footer template placed as a regular page section: the storefront
        # renders it in the header (footer) area next to the store's own — a second header —
        # and the server may also copy it as the store header onto pages it adds later.
        # Checked for new sections and rebinds only — an existing one came from the server
        # and is not re-litigated.
        if is_new or template_changed:
            slot = pages_engine.template_chrome_slot(workspace.base, section.get("template_id"))
            if (
                slot is not None
                and section.get("widget") == MYSTIQUE_WIDGET
                and section.get("section_type") == "usual"
            ):
                errors.append(
                    f"section {sid}: template {section.get('template_id')} is a store-wide {slot} "
                    f"(alias {pages_engine.CHROME_WIDGET_BY_SLOT[slot]}) placed as a regular page "
                    f"section; it belongs in the {slot} slot."
                )
        copies = ws.find_section_copies(workspace.content, sid)
        if len(copies) > 1:
            # The server keeps one record per section id — widget, template and settings —
            # and takes it from the parent copy; a child differing in any of them is lost.
            reference = tuple(copies[0][2].get(field) for field in COMPOSITE_SHARED_FIELDS)
            for page, _index, copy_section in copies[1:]:
                differing = [
                    field
                    for field, expected in zip(COMPOSITE_SHARED_FIELDS, reference)
                    if copy_section.get(field) != expected
                ]
                if differing:
                    errors.append(
                        f"section {sid}: composite copies diverge between pages in "
                        f"{', '.join(differing)} (page {page.get('alias') or page['id']}); the "
                        "backend keeps only the parent copy's widget, template and settings, so a "
                        "diverging copy would be silently lost. Edit through 'section set', which "
                        "syncs all copies."
                    )
                    break
    errors.extend(hidden_status_errors(workspace))
    return errors


def hidden_status_errors(workspace: ws.Workspace) -> "list[str]":
    """A hidden section copy whose widget the server refuses to store hidden.

    The server checks every section of the write, not only the edited ones, and answers
    the whole of it with 400. So a copy that already came hidden from the server (stored
    before the check existed) fails the write just the same: it is refused here too, with
    the way out — show it or take it off the page — instead of a 400 after confirmation.
    """
    base_copies: "dict[tuple[object, object], dict]" = {}
    for page in validated_pages(workspace.base):
        for item in page.get("layout") or []:
            if isinstance(item, dict):
                base_copies[(page["id"], item.get("id"))] = item
    errors: "list[str]" = []
    for page in validated_pages(workspace.content):
        for item in page.get("layout") or []:
            if not isinstance(item, dict) or item.get("status") != "hidden":
                continue
            if item.get("widget") in pages_engine.HIDEABLE_SECTION_WIDGETS:
                continue
            label = f"section {item.get('id')} ({item.get('widget')}) on page {page.get('alias') or page['id']}"
            before = base_copies.get((page["id"], item.get("id")))
            if before is not None and before.get("status") == "hidden" and before.get("widget") == item.get("widget"):
                errors.append(
                    f"{label}: it is hidden in the pulled version as well, but the server checks every "
                    "section of a write and refuses the whole write with 400 while it stays hidden — "
                    "this widget cannot be hidden. Show it (section status --status published) or "
                    "remove it from the page, then push."
                )
                continue
            errors.append(
                f"{label}: this widget cannot be hidden — the server refuses the whole write with 400. "
                "Remove the section from the page instead."
            )
    return errors


def build_push_plan(
    workspace: ws.Workspace,
    commit_message: str,
    activate: bool = True,
) -> "dict[str, object]":
    """The human-facing plan: a diff summary, never the multi-hundred-KB body."""
    summary = ws.diff_summary(workspace)
    if activate:
        semantics = "full replace of all pages and global settings"
    else:
        semantics = (
            "full replace stored as an unpublished proposal version (activate: false): the server "
            "immediately re-activates a copy of the current content in the same transaction"
        )
    plan: "dict[str, object]" = {
        "operation": "PUT /constructor/content",
        "semantics": semantics,
        "activate": activate,
        "base_version_id": workspace.base_version_id,
        "commit_message": commit_message,
        "pages_before": summary["pages_before"],
        "pages_after": summary["pages_after"],
        "pages_added": summary["pages_added"],
        "pages_removed": summary["pages_removed"],
        "pages_changed": summary["pages_changed"],
        "sections_added": summary["sections_added"],
        "sections_removed": summary["sections_removed"],
        "sections_changed": summary["sections_changed"],
        "section_status_changed": summary["section_status_changed"],
        "global_settings_changed": summary["global_settings_changed"],
        "expected_pages_count": summary["pages_after"],
        "expected_sections_count": summary["sections_count"],
    }
    # Stored pages the server may refuse, SEO the storefront ignores, addresses a system
    # page may take later, sections the storefront lifts out of the page content, moved
    # page addresses; relayed before the confirmation, not blocking.
    warnings = (
        pages_engine.page_gate_warnings(workspace)
        + section_placement_warnings(workspace)
        + page_edit_warnings(summary)
        + page_removal_warnings(workspace, summary, no_publish=not activate)
        + new_page_reachability_warnings(workspace, summary)
        + slot_switch_warnings(workspace, summary)
    )
    if warnings:
        plan["warnings"] = warnings
    return plan


def slot_switch_warnings(workspace: ws.Workspace, summary: "dict[str, object]") -> "list[str]":
    """A header or footer slot whose widget changes: the store-wide warning, repeated in the plan."""
    warnings: "list[str]" = []
    for entry in summary.get("sections_changed") or []:  # type: ignore[union-attr]
        assert isinstance(entry, dict)
        if "widget_changed" not in entry:
            continue
        copies = ws.find_section_copies(workspace.content, str(entry["section_id"]))
        slot = pages_engine.chrome_slot_of(copies[0][2], workspace.base) if copies else None
        if slot is None:
            continue
        before, after = entry["widget_changed"]["before"], entry["widget_changed"]["after"]  # type: ignore[index]
        warnings.append(f"section {entry['section_id']}: " + ws.slot_switch_warning(slot, before, after))
    return warnings


def new_page_reachability_warnings(workspace: ws.Workspace, summary: "dict[str, object]") -> "list[str]":
    """A new custom page is a dead end until a menu item or a section links to it."""
    work_pages = {page["id"]: page for page in validated_pages(workspace.content)}
    warnings: "list[str]" = []
    for entry in summary.get("pages_added") or []:  # type: ignore[union-attr]
        assert isinstance(entry, dict)
        page = work_pages.get(entry.get("page_id"))
        if page is not None and not pages_engine.page_alias(page):
            warnings.append(pages_engine.reachability_note(page))
    return warnings


def page_removal_warnings(
    workspace: ws.Workspace,
    summary: "dict[str, object]",
    *,
    no_publish: bool,
) -> "list[str]":
    """A removed product/collection page template takes its assignments with it."""
    base_pages = {page["id"]: page for page in validated_pages(workspace.base)}
    warnings: "list[str]" = []
    for entry in summary.get("pages_removed") or []:  # type: ignore[union-attr]
        assert isinstance(entry, dict)
        base_page = base_pages.get(entry.get("page_id"))
        if base_page is None:
            continue
        warning = pages_engine.alternative_removal_warning(base_page, no_publish=no_publish)
        if warning is not None:
            warnings.append(warning)
    return warnings


def page_edit_warnings(summary: "dict[str, object]") -> "list[str]":
    """What a page edit breaks outside the storefront content: a moved address."""
    warnings: "list[str]" = []
    for entry in summary.get("pages_changed") or []:  # type: ignore[union-attr]
        assert isinstance(entry, dict)
        pattern = (entry.get("fields") or {}).get("pattern")
        if not isinstance(pattern, dict):
            continue
        warnings.append(
            f"page {entry.get('page_id')}: its address changes from {pattern.get('before')!r} to "
            f"{pattern.get('after')!r}. The old address stops opening — links, bookmarks and search "
            "results that point at it lead nowhere. Tell the user before confirming; a redirect from "
            "the old address is set in the cabinet after the push (Настройки → SEO → Редиректы)."
        )
    return warnings


def section_placement_warnings(workspace: ws.Workspace) -> "list[str]":
    """Sections the edit adds or rebinds onto a template the storefront renders outside the content.

    Only new sections and template rebinds: an existing section came from the server and
    already renders where it renders.
    """
    base_templates: "dict[str, object]" = {}
    for page in validated_pages(workspace.base):
        for item in page.get("layout") or []:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                base_templates.setdefault(item["id"], item.get("template_id"))
    warnings: "list[str]" = []
    for section in ws.changed_or_added_sections(workspace):
        sid = section.get("id")
        if section.get("widget") != MYSTIQUE_WIDGET:
            continue
        if sid in base_templates and base_templates[sid] == section.get("template_id"):
            continue
        warning = pages_engine.template_placement_warning(workspace.base, section.get("template_id"))
        if warning is not None:
            warnings.append(f"section {sid}: {warning}")
    return warnings


def validate_put_response(value: object) -> "dict[str, object]":
    """Validate the compact UpdatedConstructorContent response.

    Unknown extra fields are tolerated deliberately: the contract grows (the no-publish
    mode added ``active_version_id``), and a validator that demanded "no extra fields"
    would turn every server rollout into a client failure after a successful mutation.
    ``active_version_id`` itself is optional here — an old server does not send it, and
    the caller decides what its absence means for the requested mode.
    """
    response = require_mapping(value, "Update response")
    version = require_mapping(response.get("version"), "Update response.version")
    identifier = version.get("id")
    if isinstance(identifier, bool) or not isinstance(identifier, int):
        raise UsageError("Update response.version.id must be an integer.")
    for field in ("pages_count", "sections_count"):
        count = response.get(field)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise UsageError(f"Update response.{field} must be a non-negative integer.")
    if "active_version_id" in response:
        active = response["active_version_id"]
        if isinstance(active, bool) or not isinstance(active, int) or active < 1:
            raise UsageError("Update response.active_version_id must be a positive integer when present.")
    return response


def response_active_version_id(response: "dict[str, object]") -> "int | None":
    """The version active after the write, or None when the server did not report one."""
    active = response.get("active_version_id")
    if isinstance(active, bool) or not isinstance(active, int):
        return None
    return active


def count_mismatches(
    plan: "dict[str, object]",
    response: "dict[str, object]",
) -> "list[str]":
    """Compare locally computed totals with what the server reports it wrote."""
    mismatches = []
    for local_key, remote_key in (
        ("expected_pages_count", "pages_count"),
        ("expected_sections_count", "sections_count"),
    ):
        if plan[local_key] != response[remote_key]:
            mismatches.append(
                f"{remote_key}: server wrote {response[remote_key]}, locally expected {plan[local_key]}"
            )
    return mismatches
