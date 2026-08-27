"""Small robust-statistics helpers shared by online feedback adapters."""

from __future__ import annotations

from statistics import median
from typing import Sequence


def winsorize(values: Sequence[float], mad_threshold: float) -> tuple[float, ...]:
    """Clip extremes with median/MAD and an IQR fallback for zero MAD."""

    if not values:
        raise ValueError("稳健统计样本不能为空")
    center = median(values)
    mad = median(abs(value - center) for value in values)
    if mad > 0:
        spread = 1.4826 * mad
        lower = center - mad_threshold * spread
        upper = center + mad_threshold * spread
    else:
        q1 = quantile(values, 0.25)
        q3 = quantile(values, 0.75)
        iqr = q3 - q1
        if iqr > 0:
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
        else:
            lower = upper = center
    return tuple(min(upper, max(lower, value)) for value in values)


def quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("分位数样本不能为空")
    if not 0 <= probability <= 1:
        raise ValueError("分位数概率必须位于 [0, 1]")
    ordered = sorted(values)
    rank = probability * (len(ordered) - 1)
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction
