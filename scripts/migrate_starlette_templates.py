#!/usr/bin/env python3
"""
One-time migration: Update Starlette TemplateResponse calls to the >= 1.0 API.

OLD:  templates.TemplateResponse("name.html", {"request": request, ...})
NEW:  templates.TemplateResponse(request, "name.html", {...})

Run:  python scripts/migrate_starlette_templates.py
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def migrate_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    original = text

    # 1. Remove multi-line `"request": request,` entries (whole line)
    text = re.sub(r'[ \t]*"request": request,\r?\n', '', text)
    # 2. Remove single-line `"request": request, ` entries
    text = re.sub(r'"request": request, ', '', text)
    # 3. Edge cases: request as last/only key in dict
    text = re.sub(r',\s*"request": request(\s*\})', r'\1', text)
    text = re.sub(r'\{\s*"request": request\s*\}', '{}', text)

    # 4. Insert `request,` as first arg for MULTI-line calls
    text = re.sub(
        r'(templates\.TemplateResponse\()\r?\n([ \t]+)(")',
        r'\1\n\2request,\n\2\3',
        text,
    )
    # 5. Insert `request,` for SINGLE-line calls: TemplateResponse("name", {...})
    text = re.sub(
        r'(templates\.TemplateResponse\()(")',
        r'\1request, \2',
        text,
    )

    if text != original:
        path.write_text(text, encoding="utf-8")
        return original.count("templates.TemplateResponse")
    return 0


def main():
    targets = set()
    app_dir = ROOT / "app"
    for py in app_dir.rglob("*.py"):
        try:
            if "TemplateResponse" in py.read_text(encoding="utf-8"):
                targets.add(py)
        except Exception:
            continue

    if not targets:
        print("No TemplateResponse calls found. Nothing to do.")
        return

    total = 0
    for f in sorted(targets):
        n = migrate_file(f)
        if n:
            print(f"  ✓ migrated {f.relative_to(ROOT)}: {n} call(s)")
            total += n

    print(f"\nDone. Migrated {total} TemplateResponse call(s) across {len(targets)} file(s).")
    print("Review changes with:  git diff")


if __name__ == "__main__":
    main()