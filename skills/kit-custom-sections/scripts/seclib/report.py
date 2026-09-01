"""Отчёт по находкам гейтов: человекочитаемый и --json."""

from __future__ import annotations

import json


def print_findings(findings: list[dict], as_json: bool = False) -> int:
    """Печать + код выхода: 2 при любой ошибке, иначе 0."""
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    if as_json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "errors": len(errors),
                    "warnings": len(warnings),
                    "findings": findings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for f in findings:
            mark = "ERROR" if f["severity"] == "error" else "warn "
            print(f"[{f['code']}] {mark} {f['where']}: {f['what']}")
            if f.get("fix"):
                print(f"         fix: {f['fix']}")
        print(f"lint: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 2 if errors else 0
