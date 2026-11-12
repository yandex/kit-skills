"""Отчёт по находкам гейтов: человекочитаемый и --json."""

from __future__ import annotations

import json


def print_findings(findings: list[dict], as_json: bool = False) -> int:
    """Печать + код выхода: 2 при любой ошибке, иначе 0."""
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    infos = [f for f in findings if f["severity"] == "info"]
    if as_json:
        print(
            json.dumps(
                {
                    "ok": not errors,
                    "errors": len(errors),
                    "warnings": len(warnings),
                    "infos": len(infos),
                    "findings": findings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        marks = {"error": "ERROR", "warning": "warn ", "info": "info "}
        for f in findings:
            mark = marks.get(f["severity"], "warn ")
            print(f"[{f['code']}] {mark} {f['where']}: {f['what']}")
            if f.get("fix"):
                print(f"         fix: {f['fix']}")
        tail = f", {len(infos)} info" if infos else ""
        print(f"lint: {len(errors)} error(s), {len(warnings)} warning(s){tail}")
    return 2 if errors else 0
