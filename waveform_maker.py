from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class ChannelConfig:
    channel_name: str
    name: str
    waveform: str
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
    independent: bool
    link_next: bool


def build_v_range(cfg: ChannelConfig, square_final_low: bool = True) -> np.ndarray:
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
    for _dt in dt_list:
        for _rep in range(repeat):
            seq = iterate_groups(groups, v_ranges)
            sequence.extend(seq)
            if round_delay > 0:
                sequence.append(tuple(v[-1] for v in v_ranges))

    dt = dt_list[0]
    t = np.arange(len(sequence)) * dt

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

    group_iters: list[list[tuple[float, ...]]] = []
    for group in groups:
        group_ranges = [v_ranges[i] for i in group]
        max_len = max(len(r) for r in group_ranges)
        padded = [
            np.pad(r, (0, max_len - len(r)), mode="edge") for r in group_ranges
        ]
        group_iters.append(list(zip(*padded)))

    plan: list[dict[str, Any]] = []
    for dt_in in dt_list:
        for _rep in range(repeat):
            for combo in itertools.product(*group_iters):
                flat = [0.0] * len(v_ranges)
                for group, values in zip(groups, combo):
                    for idx, val in zip(group, values):
                        flat[idx] = val
                plan.append({"type": "measure", "dt": dt_in, "volt": tuple(flat)})
            if round_delay > 0:
                plan.append({"type": "sleep", "seconds": round_delay})
    return plan


def find_resume_index(
    plan: list[dict[str, Any]], last_volt: tuple[float, ...]
) -> int | None:
    best_idx = None
    best_dist = None
    for idx, entry in enumerate(plan):
        if entry.get("type") != "measure":
            continue
        volt = entry.get("volt")
        if volt is None or len(volt) != len(last_volt):
            continue
        dist = sum((a - b) ** 2 for a, b in zip(volt, last_volt))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_idx = idx
    return best_idx
