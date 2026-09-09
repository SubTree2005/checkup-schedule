"""Small adapters between generated indoor-GIS data and scheduler models."""

from __future__ import annotations

import csv
from pathlib import Path

from .models import TravelTimeMatrix


def load_travel_time_matrix_csv(
    path: str | Path,
    *,
    default_minutes: int = 6,
) -> TravelTimeMatrix:
    """Load a generated origin/destination CSV into ``TravelTimeMatrix``."""

    if default_minutes < 0:
        raise ValueError("默认步行分钟数不能为负数")
    edges: dict[tuple[str, str], int] = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"origin", "destination", "travel_minutes"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"步行矩阵缺少列: {', '.join(sorted(missing))}")
        for row_number, row in enumerate(reader, start=2):
            origin = row["origin"].strip()
            destination = row["destination"].strip()
            if not origin or not destination:
                raise ValueError(f"第 {row_number} 行的位置 ID 不能为空")
            if origin == destination:
                continue
            try:
                minutes = int(row["travel_minutes"])
            except ValueError as error:
                raise ValueError(f"第 {row_number} 行的步行分钟数不是整数") from error
            if minutes < 0:
                raise ValueError(f"第 {row_number} 行的步行分钟数不能为负数")
            key = origin, destination
            previous = edges.get(key)
            if previous is not None and previous != minutes:
                raise ValueError(f"第 {row_number} 行与已有边 {origin}->{destination} 冲突")
            edges[key] = minutes
    return TravelTimeMatrix(edges, default_minutes=default_minutes)

