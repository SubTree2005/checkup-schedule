from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def prerequisite_item_ids(data: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Return project prerequisites from the supported persisted field names."""
    if not data:
        return ()
    for field in ("itemIDs", "items", "requires"):
        if field not in data or data[field] in (None, []):
            continue
        values = data[field]
        if not isinstance(values, (list, tuple, set)):
            raise ValueError(f"{field} 必须是项目 ID 数组")
        if any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"{field} 只能包含非空项目 ID")
        return tuple(dict.fromkeys(values))
    return ()


def validate_prerequisite_graph(
    item_ids: Iterable[str],
    prerequisites_by_item: Mapping[str, Iterable[str]],
) -> None:
    """Reject missing prerequisites and cycles for a complete project selection."""
    selected = set(item_ids)
    prerequisites = {item_id: set(prerequisites_by_item.get(item_id, ())) for item_id in selected}
    for item_id in sorted(selected):
        missing = prerequisites[item_id] - selected
        if missing:
            raise ValueError(f"项目 {item_id} 缺少前置项目 {sorted(missing)}")

    resolved: set[str] = set()
    while len(resolved) < len(selected):
        ready = {
            item_id
            for item_id in selected - resolved
            if prerequisites[item_id].issubset(resolved)
        }
        if not ready:
            cyclic = sorted(selected - resolved)
            raise ValueError(f"项目前置关系存在循环 {cyclic}")
        resolved.update(ready)


def validate_exam_selection(
    item_ids: Iterable[str],
    prerequisites_by_item: Mapping[str, Iterable[str]],
    conflicts_by_item: Mapping[str, Iterable[str]],
) -> None:
    """Validate that one package or plan is complete and internally compatible."""
    selected = set(item_ids)
    validate_prerequisite_graph(selected, prerequisites_by_item)
    for item_id in sorted(selected):
        conflicts = set(conflicts_by_item.get(item_id, ())) & selected
        if conflicts:
            raise ValueError(f"项目 {item_id} 与所选项目 {sorted(conflicts)} 互斥")
