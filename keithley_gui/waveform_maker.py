from __future__ import annotations

import itertools
import os
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class ChannelConfig:
    channel_name: str
    name: str
    waveform: str
    measure_voltage: bool
    measure_current: bool
    start_voltage: float
    first_node: float
    second_node: float
    dV: float
    v_high: float
    v_low: float
    v_mid: float
    v_fixed: float
    n_high: int
    n_low: int
    n_mid: int
    n_ramp: int
    n_offset: int
    v_amp: float
    v_offset: float
    n_period: int
    csv_path: str
    independent: bool
    link_next: bool


def build_v_range(cfg: ChannelConfig, square_final_low: bool = True) -> np.ndarray:
    if cfg.waveform.lower() == "csv":
        return build_csv_wave(cfg)
    if cfg.waveform.lower() == "square":
        return build_square_wave(cfg, include_final_low=square_final_low)
    if cfg.waveform.lower() == "square-3":
        return build_square3_wave(cfg)
    if cfg.waveform.lower() == "sine":
        return build_sine_wave(cfg)
    if cfg.waveform.lower() == "fixed":
        return np.array([cfg.v_fixed], dtype=float)

    if cfg.dV == 0:
        return np.array([cfg.start_voltage], dtype=float)

    n = 1 + abs(int((cfg.first_node - cfg.start_voltage) / cfg.dV))
    v_range1 = np.linspace(cfg.start_voltage, cfg.first_node, n)[:-1]
    n = 1 + abs(int((cfg.second_node - cfg.first_node) / cfg.dV))
    v_range2 = np.linspace(cfg.first_node, cfg.second_node, n)[:-1]
    n = 1 + abs(int((cfg.start_voltage - cfg.second_node) / cfg.dV))
    v_range3 = np.linspace(cfg.second_node, cfg.start_voltage, n)
    return np.concatenate((v_range1, v_range2, v_range3))


def build_square_wave(cfg: ChannelConfig, include_final_low: bool = True) -> np.ndarray:
    n_high = max(0, int(cfg.n_high))
    n_low = max(0, int(cfg.n_low))
    n_ramp = max(0, int(cfg.n_ramp))
    v_high = float(cfg.v_high)
    v_low = float(cfg.v_low)
    n_offset = int(cfg.n_offset)

    if n_ramp > 0:
        ramp_up = np.linspace(v_low, v_high, n_ramp + 2)[1:-1]
        ramp_down = np.linspace(v_high, v_low, n_ramp + 2)[1:-1]
    else:
        ramp_up = np.array([], dtype=float)
        ramp_down = np.array([], dtype=float)

    high = np.full(n_high, v_high, dtype=float)
    low = np.full(n_low, v_low, dtype=float)

    cycle = np.concatenate((low, ramp_up, high, ramp_down))
    if include_final_low:
        cycle = np.concatenate((cycle, low))
    if cycle.size == 0:
        return np.array([v_low], dtype=float)

    shift = n_offset % cycle.size
    if shift:
        cycle = np.concatenate((cycle[shift:], cycle[:shift]))
    return cycle


def build_square3_wave(cfg: ChannelConfig) -> np.ndarray:
    v_high = float(cfg.v_high)
    v_low = float(cfg.v_low)
    v_mid = float(cfg.v_mid)
    n_high = max(0, int(cfg.n_high))
    n_low = max(0, int(cfg.n_low))
    n_mid = max(0, int(cfg.n_mid))
    n_offset = int(cfg.n_offset)

    mid = np.full(n_mid, v_mid, dtype=float)
    low = np.full(n_low, v_low, dtype=float)
    high = np.full(n_high, v_high, dtype=float)

    cycle = np.concatenate((mid, low, mid, high))
    if cycle.size == 0:
        return np.array([v_mid], dtype=float)

    shift = n_offset % cycle.size
    if shift:
        cycle = np.concatenate((cycle[shift:], cycle[:shift]))
    return cycle


def build_sine_wave(cfg: ChannelConfig) -> np.ndarray:
    v_amp = float(cfg.v_amp)
    v_offset = float(cfg.v_offset)
    n_period = max(1, int(cfg.n_period))
    t = np.linspace(0, 2 * np.pi, n_period, endpoint=False)
    return v_offset + v_amp * np.sin(t)


def build_csv_wave(cfg: ChannelConfig) -> np.ndarray:
    path = cfg.csv_path.strip()
    if not path:
        raise ValueError("CSV waveform selected but no file path provided.")
    if not os.path.isfile(path):
        raise ValueError(f"CSV waveform file not found: {path}")
    data = np.loadtxt(path, delimiter=",", dtype=float)
    if data.ndim > 1:
        data = data[:, 0]
    data = data[np.isfinite(data)]
    if data.size == 0:
        raise ValueError(f"CSV waveform file has no numeric values: {path}")
    return data


def build_groups(configs: list[ChannelConfig]) -> list[list[int]]:
    groups: list[list[int]] = []
    current: list[int] = []
    for idx, cfg in enumerate(configs):
        current.append(idx)
        if not cfg.link_next:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def iterate_groups(
    groups: list[list[int]], v_ranges: list[np.ndarray]
) -> list[tuple[float, ...]]:
    group_iters: list[list[tuple[float, ...]]] = []
    for group in groups:
        group_ranges = [v_ranges[i] for i in group]
        max_len = max(len(r) for r in group_ranges)
        padded = [
            np.pad(r, (0, max_len - len(r)), mode="edge") for r in group_ranges
        ]
        group_iters.append(list(zip(*padded)))

    sequence: list[tuple[float, ...]] = []
    for combo in itertools.product(*group_iters):
        flat = [0.0] * len(v_ranges)
        for group, values in zip(groups, combo):
            for idx, val in zip(group, values):
                flat[idx] = val
        sequence.append(tuple(flat))
    return sequence


def build_traces(
    configs: list[ChannelConfig],
    dt_list: list[float],
    repeat: int,
    round_delay: float,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    v_ranges = [build_v_range(cfg, square_final_low=False) for cfg in configs]
    groups = build_groups(configs)

    sequence: list[tuple[float, ...]] = []
    t_vals: list[float] = []
    time = 0.0
    for _dt in dt_list:
        for _rep in range(repeat):
            seq = iterate_groups(groups, v_ranges)
            for item in seq:
                sequence.append(item)
                t_vals.append(time)
                time += _dt
            if round_delay > 0:
                time += round_delay
                sequence.append(tuple(v[-1] for v in v_ranges))
                t_vals.append(time)

    t = np.array(t_vals, dtype=float)

    traces: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for idx, cfg in enumerate(configs):
        traces[cfg.name] = (t, np.array([s[idx] for s in sequence], dtype=float))
    return traces


def build_plan(
    configs: list[ChannelConfig],
    dt_list: list[float],
    repeat: int,
    round_delay: float,
    square_final_low: bool = True,
) -> list[dict[str, Any]]:
    v_ranges = [build_v_range(cfg, square_final_low=square_final_low) for cfg in configs]
    groups = build_groups(configs)
    sequence = iterate_groups(groups, v_ranges)

    plan: list[dict[str, Any]] = []
    for dt_in in dt_list:
        for _rep in range(repeat):
            for volt in sequence:
                plan.append({"type": "measure", "dt": dt_in, "volt": volt})
            if round_delay > 0:
                plan.append({"type": "sleep", "seconds": round_delay})
    return plan


def find_resume_index(
    plan: list[dict[str, Any]],
    last_volt: tuple[float, ...],
    last_delta: tuple[float, ...] | None = None,
) -> int | None:
    if not plan or last_volt is None:
        return None

    if last_delta is not None and len(last_delta) != len(last_volt):
        last_delta = None

    prev_measure_idx: list[int | None] = [None] * len(plan)
    last_measure: int | None = None
    for idx, entry in enumerate(plan):
        prev_measure_idx[idx] = last_measure
        if entry.get("type") == "measure":
            last_measure = idx

    best_dist: float | None = None
    candidates: list[tuple[int, float]] = []
    for idx, entry in enumerate(plan):
        if entry.get("type") != "measure":
            continue
        volt = entry.get("volt")
        if volt is None or len(volt) != len(last_volt):
            continue
        dist = sum((a - b) ** 2 for a, b in zip(volt, last_volt))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            candidates = [(idx, dist)]
        elif best_dist is not None:
            tol = max(1e-12, best_dist * 1e-6)
            if dist <= best_dist + tol:
                candidates.append((idx, dist))

    if not candidates:
        return None
    if last_delta is None:
        return candidates[0][0]

    def direction_alignment(
        candidate_idx: int, last_delta_local: tuple[float, ...]
    ) -> float | None:
        prev_idx = prev_measure_idx[candidate_idx]
        if prev_idx is None:
            return None
        prev_entry = plan[prev_idx]
        curr_entry = plan[candidate_idx]
        prev_volt = prev_entry.get("volt")
        curr_volt = curr_entry.get("volt")
        if prev_volt is None or curr_volt is None:
            return None
        delta = tuple(c - p for c, p in zip(curr_volt, prev_volt))
        last_norm = sum(d * d for d in last_delta_local) ** 0.5
        delta_norm = sum(d * d for d in delta) ** 0.5
        if last_norm < 1e-12 or delta_norm < 1e-12:
            return None
        dot = sum(a * b for a, b in zip(last_delta_local, delta))
        return dot / (last_norm * delta_norm)

    best_idx = candidates[0][0]
    best_align: float | None = None
    for idx, _dist in candidates:
        align = direction_alignment(idx, last_delta)
        if align is None:
            continue
        if best_align is None or align > best_align:
            best_align = align
            best_idx = idx

    return best_idx
