"""Static integrity checks for the WeChat mini-program bundle."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "apps" / "miniprogram"
APP_JSON = ROOT / "app.json"


def resolve_module(source: Path, value: str) -> Path | None:
    if not value.startswith("."):
        return None
    candidate = (source.parent / value).resolve()
    if candidate.suffix:
        return candidate
    for suffix in (".js", ".json", ".wxml", ".wxss"):
        with_suffix = candidate.with_suffix(suffix)
        if with_suffix.exists():
            return with_suffix
    return candidate.with_suffix(".js")


def resolve_asset(source: Path, value: str) -> Path | None:
    if value.startswith(("http://", "https://", "data:")):
        return None
    if value.startswith("/"):
        return ROOT / value.lstrip("/")
    if value.startswith("."):
        return (source.parent / value).resolve()
    return None


def main() -> int:
    config = json.loads(APP_JSON.read_text(encoding="utf-8"))
    pages = set(config.get("pages", []))
    errors: list[str] = []

    for page in sorted(pages):
        base = ROOT / page
        for suffix in (".js", ".json", ".wxml", ".wxss"):
            target = base.with_suffix(suffix)
            if not target.is_file():
                errors.append(f"missing page file: {target.relative_to(ROOT)}")

    source_files = [ROOT / "app.js", APP_JSON, ROOT / "app.wxss"]
    source_files.extend((ROOT / "utils").rglob("*.js"))
    source_files.extend((ROOT / "custom-tab-bar").rglob("*.*"))
    for page in sorted(pages):
        base = ROOT / page
        source_files.extend(base.with_suffix(suffix) for suffix in (".js", ".json", ".wxml", ".wxss"))
    for source in source_files:
        text = source.read_text(encoding="utf-8")
        for value in re.findall(r"require\(['\"]([^'\"]+)['\"]\)", text):
            target = resolve_module(source, value)
            if target is not None and not target.is_file():
                errors.append(
                    f"missing module: {source.relative_to(ROOT)} -> {value}"
                )
        for value in re.findall(r"(?:src|iconPath|selectedIconPath)=?['\"]([^'\"{]+)['\"]", text):
            target = resolve_asset(source, value)
            if target is not None and not target.is_file():
                errors.append(
                    f"missing asset: {source.relative_to(ROOT)} -> {value}"
                )
        for route in re.findall(r"/pages/[a-z0-9-]+/[a-z0-9-]+", text):
            normalized = route.lstrip("/")
            if normalized not in pages:
                errors.append(
                    f"unregistered route: {source.relative_to(ROOT)} -> {route}"
                )

    for page in sorted(pages):
        base = ROOT / page
        script = base.with_suffix(".js").read_text(encoding="utf-8")
        markup = base.with_suffix(".wxml").read_text(encoding="utf-8")
        handlers = set(
            re.findall(
                r"(?:bind|catch)(?:tap|input|change|submit|confirm|blur|focus)=['\"]([A-Za-z_$][\w$]*)['\"]",
                markup,
            )
        )
        for handler in sorted(handlers):
            if not re.search(rf"\b{re.escape(handler)}\s*\(", script):
                errors.append(f"missing handler: {page}.wxml -> {handler}()")

    tab_pages = {
        item.get("pagePath") for item in config.get("tabBar", {}).get("list", [])
    }
    for page in tab_pages:
        if page and page not in pages:
            errors.append(f"tab page is not registered: {page}")

    errors = sorted(set(errors))
    if errors:
        print("Mini-program integrity audit failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print(
        f"Mini-program integrity audit passed: {len(pages)} pages, "
        f"{len(source_files)} source files checked."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
