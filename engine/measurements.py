"""
Oscilloscope-style waveform measurements over a sampled signal.

Every function takes matched (times, values) sequences and returns plain
floats (or None when a measurement is not defined for the given signal, e.g.
a frequency measurement on a monotonic step). Threshold crossings are found
by linear interpolation between samples, so results are continuous in the
sample values rather than quantized to the time grid. Integral quantities
(mean, RMS) use trapezoidal time-weighting, which is correct on the
non-uniform grids produced by the adaptive transient solver.
"""
import math
from typing import Any, Dict, List, Optional, Sequence, Tuple


def _clean(times: Sequence[float], values: Sequence[float]) -> Tuple[List[float], List[float]]:
    if len(times) != len(values):
        raise ValueError("times and values must have equal length")
    if len(times) < 2:
        raise ValueError("at least two samples are required")
    return [float(t) for t in times], [float(v) for v in values]


def _interp_crossing(t0: float, v0: float, t1: float, v1: float, level: float) -> float:
    """Time between (t0,v0) and (t1,v1) where the segment crosses level."""
    if v1 == v0:
        return t0
    return t0 + (level - v0) * (t1 - t0) / (v1 - v0)


def crossings(times: Sequence[float], values: Sequence[float], level: float,
              direction: str = "both") -> List[float]:
    """
    Interpolated times at which the signal crosses `level`.

    direction: "rising", "falling", or "both". A sample sitting exactly on
    the level is treated as a crossing only when the following segment
    departs in the requested direction, so a flat run does not emit a
    crossing per sample.
    """
    ts, vs = _clean(times, values)
    out: List[float] = []
    for i in range(len(ts) - 1):
        v0, v1 = vs[i], vs[i + 1]
        rising = v0 < level <= v1
        falling = v0 > level >= v1
        if direction in ("rising", "both") and rising:
            out.append(_interp_crossing(ts[i], v0, ts[i + 1], v1, level))
        elif direction in ("falling", "both") and falling:
            out.append(_interp_crossing(ts[i], v0, ts[i + 1], v1, level))
    return out


def basic_stats(times: Sequence[float], values: Sequence[float]) -> Dict[str, float]:
    """Min, max, peak-to-peak, time-weighted average, and RMS."""
    ts, vs = _clean(times, values)
    vmin, vmax = min(vs), max(vs)

    total_t = ts[-1] - ts[0]
    if total_t <= 0.0:
        avg = sum(vs) / len(vs)
        rms = math.sqrt(sum(v * v for v in vs) / len(vs))
    else:
        area = 0.0
        area_sq = 0.0
        for i in range(len(ts) - 1):
            dt = ts[i + 1] - ts[i]
            area += 0.5 * (vs[i] + vs[i + 1]) * dt
            area_sq += 0.5 * (vs[i] ** 2 + vs[i + 1] ** 2) * dt
        avg = area / total_t
        rms = math.sqrt(max(0.0, area_sq / total_t))

    return {
        "min": vmin,
        "max": vmax,
        "peak_to_peak": vmax - vmin,
        "average": avg,
        "rms": rms,
    }


def rise_time(times: Sequence[float], values: Sequence[float],
              low_pct: float = 0.1, high_pct: float = 0.9) -> Optional[float]:
    """10%-90% rise time of the first rising edge across the full swing."""
    ts, vs = _clean(times, values)
    vmin, vmax = min(vs), max(vs)
    swing = vmax - vmin
    if swing <= 0.0:
        return None
    lo = crossings(ts, vs, vmin + low_pct * swing, "rising")
    if not lo:
        return None
    hi = [t for t in crossings(ts, vs, vmin + high_pct * swing, "rising") if t >= lo[0]]
    if not hi:
        return None
    return hi[0] - lo[0]


def fall_time(times: Sequence[float], values: Sequence[float],
              low_pct: float = 0.1, high_pct: float = 0.9) -> Optional[float]:
    """90%-10% fall time of the first falling edge across the full swing."""
    ts, vs = _clean(times, values)
    vmin, vmax = min(vs), max(vs)
    swing = vmax - vmin
    if swing <= 0.0:
        return None
    hi = crossings(ts, vs, vmin + high_pct * swing, "falling")
    if not hi:
        return None
    lo = [t for t in crossings(ts, vs, vmin + low_pct * swing, "falling") if t >= hi[0]]
    if not lo:
        return None
    return lo[0] - hi[0]


def overshoot_pct(times: Sequence[float], values: Sequence[float]) -> float:
    """
    Percent overshoot of a step response relative to its final level,
    using the first and last samples as the initial and final values.
    Zero when the response does not exceed its final level.
    """
    _, vs = _clean(times, values)
    initial, final = vs[0], vs[-1]
    step = final - initial
    if step == 0.0:
        return 0.0
    if step > 0.0:
        return max(0.0, (max(vs) - final) / step) * 100.0
    return max(0.0, (final - min(vs)) / (-step)) * 100.0


def settling_time(times: Sequence[float], values: Sequence[float],
                  tol: float = 0.02) -> Optional[float]:
    """
    Time (from the record start) after which the signal stays within
    +/-tol of its final value. None if it never settles within the record.
    """
    ts, vs = _clean(times, values)
    final = vs[-1]
    initial = vs[0]
    ref = abs(final - initial)
    band = tol * (ref if ref > 0.0 else max(1e-12, abs(final)))

    last_outside = None
    for i, v in enumerate(vs):
        if abs(v - final) > band:
            last_outside = i
    if last_outside is None:
        return 0.0
    if last_outside >= len(ts) - 1:
        return None
    return ts[last_outside + 1] - ts[0]


def period_frequency(times: Sequence[float], values: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """
    Period and frequency from the average spacing of rising mid-level
    crossings. Returns (None, None) for signals without two full cycles.
    """
    ts, vs = _clean(times, values)
    mid = 0.5 * (max(vs) + min(vs))
    rising = crossings(ts, vs, mid, "rising")
    if len(rising) < 2:
        return None, None
    spacings = [rising[i + 1] - rising[i] for i in range(len(rising) - 1)]
    period = sum(spacings) / len(spacings)
    if period <= 0.0:
        return None, None
    return period, 1.0 / period


def duty_cycle(times: Sequence[float], values: Sequence[float]) -> Optional[float]:
    """
    Fraction of one period spent above the mid level, measured between the
    first and last rising crossings so partial cycles do not skew it.
    """
    ts, vs = _clean(times, values)
    mid = 0.5 * (max(vs) + min(vs))
    rising = crossings(ts, vs, mid, "rising")
    if len(rising) < 2:
        return None

    t_start, t_end = rising[0], rising[-1]
    window = t_end - t_start
    if window <= 0.0:
        return None

    # Trapezoidal time above the mid level, clipped to the crossing window
    above = 0.0
    for i in range(len(ts) - 1):
        a, b = ts[i], ts[i + 1]
        if b <= t_start or a >= t_end:
            continue
        va, vb = vs[i], vs[i + 1]
        # Clip the segment to [t_start, t_end]
        if a < t_start:
            va = va + (vb - va) * (t_start - a) / (b - a)
            a = t_start
        if b > t_end:
            vb = va + (vb - va) * (t_end - a) / (b - a) if b != a else vb
            b = t_end
        above += _segment_time_above(a, va, b, vb, mid)
    return above / window


def _segment_time_above(a: float, va: float, b: float, vb: float, level: float) -> float:
    """Duration within [a,b] for which the linear segment sits above level."""
    dt = b - a
    if dt <= 0.0:
        return 0.0
    hi_a, hi_b = va >= level, vb >= level
    if hi_a and hi_b:
        return dt
    if not hi_a and not hi_b:
        return 0.0
    tc = _interp_crossing(a, va, b, vb, level)
    return (b - tc) if hi_b else (tc - a)


def measure_all(times: Sequence[float], values: Sequence[float]) -> Dict[str, Any]:
    """Runs the full measurement set, omitting undefined results (None)."""
    result: Dict[str, Any] = dict(basic_stats(times, values))
    rt = rise_time(times, values)
    if rt is not None:
        result["rise_time"] = rt
    ft = fall_time(times, values)
    if ft is not None:
        result["fall_time"] = ft
    result["overshoot_pct"] = overshoot_pct(times, values)
    st = settling_time(times, values)
    if st is not None:
        result["settling_time"] = st
    period, freq = period_frequency(times, values)
    if period is not None:
        result["period"] = period
        result["frequency"] = freq
        duty = duty_cycle(times, values)
        if duty is not None:
            result["duty_cycle"] = duty
    return result
