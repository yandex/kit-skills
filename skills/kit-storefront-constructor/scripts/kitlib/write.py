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


def page_for_put(page: "dict[str, object]") -> "dict[str, object]":
    """Convert one read-shape page into the accepted write shape."""
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


def _validate_section_settings(
    section: "dict[str, object]",
    workspace: ws.Workspace,
    schema_document: "dict[str, object] | None",
) -> "list[str]":
    """Validate one changed section's settings against its authoritative schema."""
    import json

    widget = section.get("widget")
    settings = section.get("settings")
    label = f"section {section.get('id')} ({widget})"
    if widget == MYSTIQUE_WIDGET:
        template_id = section.get("template_id")
        if not isinstance(template_id, str) or not template_id:
            return [f"{label}: a {MYSTIQUE_WIDGET} section requires template_id"]
        try:
            template = ws.section_template(workspace.base, template_id)
        except UsageError as error:
            return [f"{label}: {error}"]
        raw_schema = template.get("json_schema")
        if not isinstance(raw_schema, str):
            return [f"{label}: template {template_id} json_schema must be a string"]
        try:
            decoded = json.loads(raw_schema)
        except json.JSONDecodeError as error:
            return [f"{label}: template {template_id} json_schema is invalid JSON: {error.msg}"]
        if not isinstance(decoded, dict):
            return [f"{label}: template {template_id} json_schema must decode to an object"]
        # Live template schemas resolve editor models (ImageModel, LinkSource, TextArea, …)
        # against the BFF definitions, and fragment refs against `#/definitions/partials/…`,
        # which exists nowhere as a schema — fragments carry their own schema server-side.
        # Combined resolution: own definitions win, BFF definitions fill the editor models,
        # and partials refs are treated as unconstrained rather than failing every template.
        decoded = _neutralize_partials_refs(decoded)
        template_definitions = decoded.get("definitions")
        template_definitions = template_definitions if isinstance(template_definitions, dict) else {}
        combined_definitions: "dict[str, object]" = {}
        if schema_document is not None:
            bff_definitions = schema_document.get("definitions")
            if isinstance(bff_definitions, dict):
                combined_definitions.update(bff_definitions)
        combined_definitions.update(template_definitions)
        instance = settings if settings is not None else {}
        return [
            f"{label}: {message}"
            for message in schema_engine.validate_instance(instance, decoded, combined_definitions)
        ]
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
    instance = settings if settings is not None else {}
    return [
        f"{label}: {message}"
        for message in schema_engine.validate_instance(instance, node, definitions)
    ]


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
    # workspace can neither be written nor previewed through push. No editing command
    # touches templates; a difference here means the workspace was hand-edited.
    if workspace.content.get("section_templates") != workspace.base.get("section_templates"):
        errors.append(
            "section_templates differ from the base snapshot; PUT /constructor/content does not "
            "transmit templates, so this change can neither be written nor previewed. Templates "
            "live outside content versions: to change a custom section's markup, create a NEW "
            "template with 'templates create', pull again so the snapshot holds it, and move the "
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

    # Gates: page-level rules the server enforces (patterns, aliases, variants,
    # statuses, SEO meta) — added or changed pages only, mirrored in kitlib/pages.py.
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
            baseline: "tuple[str, ...]" = ()
            if not is_new and schema_document is not None:
                # Baseline only when a schema is actually available: the fail-closed
                # "no cached schema" error must never cancel itself out. It is built from
                # the base section as the server had it — template included — because
                # judging the old settings under the *new* template would whitelist
                # exactly the breakage a rebind is meant to surface.
                baseline = tuple(_validate_section_settings(base_section, workspace, schema_document))
            errors.extend(introduced_errors(section, workspace, schema_document, baseline))
        sid = str(section.get("id"))
        copies = ws.find_section_copies(workspace.content, sid)
        if len(copies) > 1:
            reference = copies[0][2].get("settings")
            for page, _index, copy_section in copies[1:]:
                if copy_section.get("settings") != reference:
                    errors.append(
                        f"section {sid}: composite copies diverge between pages "
                        f"(page {page.get('alias') or page['id']}); the backend keeps only the "
                        "parent copy's settings, so a diverging copy would be silently lost. "
                        "Edit through 'section set', which syncs all copies."
                    )
                    break
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
    return {
        "operation": "PUT /constructor/content",
        "semantics": semantics,
        "activate": activate,
        "base_version_id": workspace.base_version_id,
        "commit_message": commit_message,
        "pages_before": summary["pages_before"],
        "pages_after": summary["pages_after"],
        "pages_added": summary["pages_added"],
        "pages_removed": summary["pages_removed"],
        "sections_added": summary["sections_added"],
        "sections_removed": summary["sections_removed"],
        "sections_changed": summary["sections_changed"],
        "global_settings_changed": summary["global_settings_changed"],
        "expected_pages_count": summary["pages_after"],
        "expected_sections_count": summary["sections_count"],
    }


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
