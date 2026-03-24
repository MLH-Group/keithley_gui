from __future__ import annotations

import os
import threading
import time
from typing import Any

from PyQt5 import QtCore
from qcodes.dataset import initialise_or_create_database_at, load_or_create_experiment

from . import trigger_fns
from . import utilities
from .waveform_maker import ChannelConfig, build_plan, build_v_range, find_resume_index


def build_sweepers(
    configs: list[ChannelConfig],
    keithleys: dict[str, Any],
    square_final_low: bool = True,
) -> list[dict[str, Any]]:
    sweepers: list[dict[str, Any]] = []
    for cfg in configs:
        inst_name, ch_name = cfg.channel_name.split(".")
        channel = getattr(keithleys[inst_name], ch_name)
        v_range = build_v_range(cfg, square_final_low=square_final_low)
        meas_v_param = None
        if cfg.measure_voltage:
            meas_v_param = utilities.ensure_meas_v_parameter(channel)
        sweepers.append(
            {
                "channel": channel,
                "channel_name": cfg.channel_name,
                "name": cfg.name,
                "measure_voltage": cfg.measure_voltage,
                "measure_current": cfg.measure_current,
                "meas_v_param": meas_v_param,
                "first_node": cfg.first_node,
                "second_node": cfg.second_node,
                "start_voltage": cfg.start_voltage,
                "dV": cfg.dV,
                "independent": cfg.independent,
                "link_next": cfg.link_next,
                "threshold_value": float(cfg.threshold_value),
                "threshold_action": str(cfg.threshold_action).strip().lower(),
                "v_range": v_range,
            }
        )

    return sweepers


def resolve_csv_path(base: str, device: str, exp: str, run_id: int) -> str:
    if base.endswith(".csv"):
        return base
    return os.path.join(base, f"{device}{exp}_{run_id}_manual_sweep.csv")


def _iter_linked_groups(
    sweepers: list[dict[str, Any]],
    targets: list[float],
) -> list[tuple[list[dict[str, Any]], list[float]]]:
    if len(sweepers) != len(targets):
        raise ValueError("sweepers and targets length mismatch")

    groups: list[tuple[list[dict[str, Any]], list[float]]] = []
    current_sweepers: list[dict[str, Any]] = []
    current_targets: list[float] = []

    for sweeper, target in zip(sweepers, targets):
        current_sweepers.append(sweeper)
        current_targets.append(float(target))
        if not bool(sweeper.get("link_next", False)):
            groups.append((current_sweepers, current_targets))
            current_sweepers = []
            current_targets = []

    if current_sweepers:
        groups.append((current_sweepers, current_targets))
    return groups


def _ramp_channels_together(
    channels: list[Any],
    targets: list[float],
    ramp_dv: float,
    ramp_dt: float,
) -> None:
    initial = [float(ch.volt()) for ch in channels]
    targets_f = [float(v) for v in targets]
    max_delta = max(abs(vf - v0) for v0, vf in zip(initial, targets_f))
    n_steps = max(1, int(1 + (max_delta / ramp_dv)))

    for step in range(1, n_steps + 1):
        frac = step / n_steps
        for ch, v0, vf in zip(channels, initial, targets_f):
            ch.volt(v0 + (vf - v0) * frac)
        if ramp_dt > 0:
            time.sleep(ramp_dt)


def ramp_sweepers_with_linking(
    sweepers: list[dict[str, Any]],
    targets: list[float],
    ramp_dv: float,
    ramp_dt: float,
) -> None:
    for group_sweepers, group_targets in _iter_linked_groups(sweepers, targets):
        if len(group_sweepers) <= 1:
            sweeper = group_sweepers[0]
            utilities.ramp_voltage(
                sweeper["channel"],
                group_targets[0],
                rampdV=ramp_dv,
                rampdT=ramp_dt,
            )
            continue

        channels = [s["channel"] for s in group_sweepers]
        _ramp_channels_together(channels, group_targets, ramp_dv, ramp_dt)


class RunWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    status = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)
    threshold_event = QtCore.pyqtSignal(str)

    def __init__(
        self,
        station,
        keithleys: dict[str, Any],
        configs: list[ChannelConfig],
        dt_list: list[float],
        delay_ratio: float,
        repeat: int,
        round_delay: float,
        db_path: str,
        exp_name: str,
        device_name: str,
        run_name: str,
        csv_path: str,
        ramp_up: bool,
        ramp_down: bool,
        ramp_dv: float,
        ramp_dt: float,
        time_independent: bool,
    ) -> None:
        super().__init__()
        self.station = station
        self.keithleys = keithleys
        self.configs = configs
        self.dt_list = dt_list
        self.delay_ratio = delay_ratio
        self.repeat = repeat
        self.round_delay = round_delay
        self.db_path = db_path
        self.exp_name = exp_name
        self.device_name = device_name
        self.run_name = run_name
        self.csv_path = csv_path
        self.ramp_up = ramp_up
        self.ramp_down = ramp_down
        self.ramp_dv = ramp_dv
        self.ramp_dt = ramp_dt
        self.time_independent = time_independent

        self._pause_event = threading.Event()
        self._pause_event.set()
        self._rebuild_on_resume = False
        self._step_index = 0
        self.is_paused = False
        self._stop_requested = False
        self._last_volt: tuple[float, ...] | None = None
        self._prev_measure_volt: tuple[float, ...] | None = None
        self._last_delta: tuple[float, ...] | None = None
        self._visa_overhead_s = 0.0
        self._min_programmed_step_s = 1e-3
        self._reprogram_threshold_s = 2e-4

    @QtCore.pyqtSlot()
    def request_pause(self) -> None:
        self.is_paused = True
        self._pause_event.clear()
        self.status.emit("Paused")

    def request_resume(
        self,
        configs: list[ChannelConfig],
        dt_list: list[float],
        repeat: int,
        round_delay: float,
        delay_ratio: float,
    ) -> None:
        self.configs = configs
        self.dt_list = dt_list
        self.repeat = repeat
        self.round_delay = round_delay
        self.delay_ratio = delay_ratio
        self._rebuild_on_resume = True
        self.is_paused = False
        self._pause_event.set()
        self.status.emit("Running")

    def request_stop(self) -> None:
        self._stop_requested = True
        self._pause_event.set()

    def run(self) -> None:
        try:
            self.status.emit("Running")
            initialise_or_create_database_at(self.db_path)
            test_exp = load_or_create_experiment(
                experiment_name=self.exp_name,
                sample_name=self.device_name,
            )

            sweepers = build_sweepers(self.configs, self.keithleys)
            meas_forward, time_param, _indep = utilities.setup_database_registers_arb(
                self.station,
                test_exp,
                sweepers,
                time_independent=self.time_independent,
                measurement_name=self.run_name or "forward",
            )
            meas_forward.write_period = 2

            if self.ramp_up:
                ramp_targets = [float(sweeper["v_range"][0]) for sweeper in sweepers]
                ramp_sweepers_with_linking(
                    sweepers,
                    ramp_targets,
                    self.ramp_dv,
                    self.ramp_dt,
                )

            for sweeper in sweepers:
                ch = sweeper["channel"]
                trigger_fns.source_trig_params(ch)
                initial_mode = "i"
                if sweeper.get("measure_voltage") and not sweeper.get("measure_current"):
                    initial_mode = "v"
                trigger_fns.meas_trig_params(ch, initial_mode)

            plan = build_plan(self.configs, self.dt_list, self.repeat, self.round_delay)
            split_for_dual = self._has_dual_measurement(sweepers)
            prime_start = time.perf_counter()
            last_dt = self._prime_initial_measurement(sweepers, plan, split_for_dual)
            prime_elapsed = time.perf_counter() - prime_start
            self._calibrate_visa_overhead(last_dt, prime_elapsed, sweepers)
            last_split_for_dual = split_for_dual
            last_programmed_dt = last_dt
            next_measure_deadline = time.perf_counter()
            time_param.reset_clock()

            with meas_forward.run() as forward_saver:
                while self._step_index < len(plan):
                    if self._stop_requested:
                        break
                    self._pause_event.wait()

                    if self._rebuild_on_resume:
                        sweepers = build_sweepers(self.configs, self.keithleys)
                        plan = build_plan(
                            self.configs, self.dt_list, self.repeat, self.round_delay
                        )
                        if self._last_volt is not None:
                            resume_idx = find_resume_index(
                                plan, self._last_volt, self._last_delta
                            )
                            if resume_idx is not None:
                                self._step_index = resume_idx
                        if self._step_index >= len(plan):
                            break
                        self._rebuild_on_resume = False

                    entry = plan[self._step_index]
                    if entry["type"] == "sleep":
                        if self._stop_requested:
                            break
                        threading.Event().wait(entry["seconds"])
                        next_measure_deadline = time.perf_counter()
                        self._step_index += 1
                        continue

                    dt_in = entry["dt"]
                    now = time.perf_counter()
                    if now < next_measure_deadline:
                        threading.Event().wait(next_measure_deadline - now)

                    split_for_dual = self._has_dual_measurement(sweepers)
                    has_measurement = self._has_any_measurement(sweepers)
                    programmed_dt = dt_in
                    if has_measurement:
                        programmed_dt = max(
                            self._min_programmed_step_s, dt_in - self._visa_overhead_s
                        )
                    if (
                        last_programmed_dt is None
                        or abs(programmed_dt - last_programmed_dt)
                        > self._reprogram_threshold_s
                        or split_for_dual != last_split_for_dual
                    ):
                        self._set_ktime(
                            sweepers,
                            programmed_dt,
                            self.delay_ratio,
                            split_for_dual=split_for_dual,
                        )
                        last_programmed_dt = programmed_dt
                        last_split_for_dual = split_for_dual

                    for x, sweeper in zip(entry["volt"], sweepers):
                        trigger_fns.set_v(sweeper["channel"], x)

                    t = time_param()
                    get_readings = []
                    independent_params = []
                    step_source_values: dict[Any, float] = {}

                    for x, sweeper in zip(entry["volt"], sweepers):
                        step_source_values[sweeper["channel"]] = float(x)

                    source_vals, measured_volt, measured_curr = (
                        self._measure_step_trigger_readings(
                            sweepers, split_for_dual=split_for_dual
                        )
                    )

                    for sweeper in sweepers:
                        ch = sweeper["channel"]
                        measure_current = bool(sweeper.get("measure_current", True))
                        measure_voltage = bool(sweeper.get("measure_voltage", False))
                        source_v = source_vals.get(ch, step_source_values.get(ch, 0.0))
                        measured_v = measured_volt.get(ch)
                        if measure_voltage and measured_v is None:
                            measured_v = self._read_voltage_direct(ch)
                            measured_volt[ch] = measured_v
                        v_used = measured_v if measured_v is not None else source_v
                        j = measured_curr.get(ch)
                        if measure_current:
                            if j is None:
                                j = 0.0
                            get_readings.append((ch.curr, j))

                        if sweeper["independent"]:
                            independent_params.append((ch.volt, source_v))
                        else:
                            get_readings.append((ch.volt, v_used))

                        if measure_voltage:
                            meas_v_param = sweeper.get("meas_v_param")
                            if meas_v_param is not None:
                                if measured_v is None:
                                    raise RuntimeError(
                                        f"No measured voltage available for {sweeper.get('channel_name', ch)}"
                                    )
                                get_readings.append((meas_v_param, measured_v))

                    forward_saver.add_result(
                        *independent_params,
                        *get_readings,
                        (time_param, t),
                    )
                    (
                        threshold_action,
                        threshold_status_msg,
                        threshold_popup_msg,
                    ) = self._evaluate_threshold_action(
                        sweepers,
                        measured_volt,
                        measured_curr,
                    )
                    if threshold_action == "stop":
                        self._stop_requested = True
                        self._pause_event.set()
                        self.status.emit(threshold_status_msg or "Stopped by threshold.")
                        self.threshold_event.emit(
                            threshold_popup_msg or threshold_status_msg or "Stopped by threshold."
                        )
                    elif threshold_action == "pause":
                        self.is_paused = True
                        self._pause_event.clear()
                        self.status.emit(threshold_status_msg or "Paused by threshold.")
                        self.threshold_event.emit(
                            threshold_popup_msg or threshold_status_msg or "Paused by threshold."
                        )
                    step_end = time.perf_counter()

                    next_measure_deadline += dt_in
                    if step_end - next_measure_deadline > dt_in:
                        next_measure_deadline = step_end
                    if entry.get("type") == "measure":
                        volt_tuple = tuple(entry.get("volt", ()))
                        if self._prev_measure_volt is not None and len(
                            volt_tuple
                        ) == len(self._prev_measure_volt):
                            delta = tuple(
                                c - p for c, p in zip(volt_tuple, self._prev_measure_volt)
                            )
                            delta_norm = sum(d * d for d in delta) ** 0.5
                            if delta_norm >= 1e-12:
                                self._last_delta = delta
                        self._prev_measure_volt = volt_tuple
                        self._last_volt = volt_tuple
                    self._step_index += 1
                    if self._stop_requested:
                        break

            data_forward = forward_saver.dataset
            if self.csv_path:
                csv_file = resolve_csv_path(
                    self.csv_path, self.device_name, self.exp_name, data_forward.run_id
                )
                data_forward.to_pandas_dataframe().to_csv(csv_file)

            if self.ramp_down:
                ramp_sweepers_with_linking(
                    sweepers,
                    [0.0] * len(sweepers),
                    self.ramp_dv,
                    self.ramp_dt,
                )

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()

    @staticmethod
    def _evaluate_threshold_action(
        sweepers: list[dict[str, Any]],
        measured_volt: dict[Any, float],
        measured_curr: dict[Any, float],
    ) -> tuple[str | None, str | None, str | None]:
        best_action: str | None = None
        best_status_message: str | None = None
        best_popup_message: str | None = None
        action_priority = {"pause": 1, "stop": 2}

        for sweeper in sweepers:
            threshold = float(sweeper.get("threshold_value", 0.0) or 0.0)
            if threshold <= 0:
                continue
            action = str(sweeper.get("threshold_action", "none")).strip().lower()
            if action not in ("pause", "stop"):
                continue

            ch = sweeper["channel"]
            channel_name = str(sweeper.get("channel_name", getattr(ch, "name", "channel")))
            display_name = str(sweeper.get("name", "")).strip() or channel_name
            measure_voltage = bool(sweeper.get("measure_voltage", False))
            measure_current = bool(sweeper.get("measure_current", True))

            candidates: list[tuple[str, float, str]] = []
            if measure_voltage and ch in measured_volt:
                candidates.append(("voltage", float(measured_volt[ch]), "V"))
            if measure_current and ch in measured_curr:
                candidates.append(("current", float(measured_curr[ch]), "A"))

            for quantity_name, value, unit in candidates:
                if abs(value) < threshold:
                    continue
                action_word = "paused" if action == "pause" else "stopped"
                status_message = (
                    f"Sweep {action_word} by threshold: {display_name} {quantity_name} "
                    f"{value:.6g}{unit} (threshold {threshold:.6g}{unit}, abs compare)."
                )
                popup_message = (
                    f"Threshold value {threshold:.6g}{unit} reached by {display_name}, "
                    f"sweep now {action_word}. "
                    f"(Measured {value:.6g}{unit})"
                )
                if (
                    best_action is None
                    or action_priority[action] > action_priority.get(best_action, 0)
                ):
                    best_action = action
                    best_status_message = status_message
                    best_popup_message = popup_message
                break

        return best_action, best_status_message, best_popup_message

    def _prime_initial_measurement(
        self,
        sweepers: list[dict[str, Any]],
        plan: list[dict[str, Any]],
        split_for_dual: bool,
    ) -> float | None:
        first_measure = next((entry for entry in plan if entry["type"] == "measure"), None)
        if first_measure is None:
            return None

        meas_v_sweepers = [s for s in sweepers if s.get("measure_voltage")]
        meas_i_sweepers = [s for s in sweepers if s.get("measure_current")]
        if not meas_v_sweepers and not meas_i_sweepers:
            return None

        dt_in = float(first_measure["dt"])
        self._set_ktime(sweepers, dt_in, self.delay_ratio, split_for_dual=split_for_dual)
        for x, sweeper in zip(first_measure["volt"], sweepers):
            trigger_fns.set_v(sweeper["channel"], x)
        self._measure_step_trigger_readings(sweepers, split_for_dual=split_for_dual)

        return dt_in

    @staticmethod
    def _read_voltage_direct(ch: Any) -> float:
        return float(ch.ask(f"{ch.channel}.measure.v()"))

    @staticmethod
    def _has_dual_measurement(sweepers: list[dict[str, Any]]) -> bool:
        return any(
            bool(s.get("measure_voltage", False)) and bool(s.get("measure_current", True))
            for s in sweepers
        )

    @staticmethod
    def _has_any_measurement(sweepers: list[dict[str, Any]]) -> bool:
        return any(
            bool(s.get("measure_voltage", False)) or bool(s.get("measure_current", True))
            for s in sweepers
        )

    def _calibrate_visa_overhead(
        self, dt_in: float | None, elapsed_s: float, sweepers: list[dict[str, Any]]
    ) -> None:
        if dt_in is None or dt_in <= 0 or not self._has_any_measurement(sweepers):
            self._visa_overhead_s = 0.0
            return

        overhead = max(0.0, elapsed_s - dt_in)
        max_reasonable = max(0.0, 0.5 * dt_in)
        self._visa_overhead_s = min(overhead, max_reasonable)

    def _measure_step_trigger_readings(
        self, sweepers: list[dict[str, Any]], split_for_dual: bool
    ) -> tuple[dict[Any, float], dict[Any, float], dict[Any, float]]:
        source_vals: dict[Any, float] = {}
        measured_volt: dict[Any, float] = {}
        measured_curr: dict[Any, float] = {}

        dual = [
            s
            for s in sweepers
            if bool(s.get("measure_voltage", False)) and bool(s.get("measure_current", True))
        ]
        v_only = [
            s
            for s in sweepers
            if bool(s.get("measure_voltage", False)) and not bool(s.get("measure_current", True))
        ]
        i_only = [
            s
            for s in sweepers
            if bool(s.get("measure_current", True)) and not bool(s.get("measure_voltage", False))
        ]

        if split_for_dual and dual:
            phase1_modes: dict[Any, str] = {}
            for sweeper in dual:
                phase1_modes[sweeper["channel"]] = "v"
            for sweeper in v_only:
                phase1_modes[sweeper["channel"]] = "v"
            for sweeper in i_only:
                phase1_modes[sweeper["channel"]] = "i"
            phase1_source, phase1_readings = self._trigger_phase(phase1_modes)

            phase2_modes: dict[Any, str] = {}
            for sweeper in dual:
                phase2_modes[sweeper["channel"]] = "i"
            for sweeper in i_only:
                phase2_modes[sweeper["channel"]] = "i"
            for sweeper in v_only:
                phase2_modes[sweeper["channel"]] = "v"
            phase2_source, phase2_readings = self._trigger_phase(phase2_modes)

            for sweeper in dual:
                ch = sweeper["channel"]
                if ch in phase1_readings:
                    measured_volt[ch] = phase1_readings[ch]
                if ch in phase2_readings:
                    measured_curr[ch] = phase2_readings[ch]

            for sweeper in v_only:
                ch = sweeper["channel"]
                if ch in phase1_readings and ch in phase2_readings:
                    measured_volt[ch] = 0.5 * (phase1_readings[ch] + phase2_readings[ch])
                elif ch in phase1_readings:
                    measured_volt[ch] = phase1_readings[ch]
                elif ch in phase2_readings:
                    measured_volt[ch] = phase2_readings[ch]

            for sweeper in i_only:
                ch = sweeper["channel"]
                if ch in phase1_readings and ch in phase2_readings:
                    measured_curr[ch] = 0.5 * (phase1_readings[ch] + phase2_readings[ch])
                elif ch in phase1_readings:
                    measured_curr[ch] = phase1_readings[ch]
                elif ch in phase2_readings:
                    measured_curr[ch] = phase2_readings[ch]

            source_vals.update(phase1_source)
            source_vals.update(phase2_source)
            return source_vals, measured_volt, measured_curr

        if v_only or dual:
            v_modes = {s["channel"]: "v" for s in sweepers if s.get("measure_voltage", False)}
            source_v, readings_v = self._trigger_phase(v_modes)
            source_vals.update(source_v)
            measured_volt.update(readings_v)

        if i_only or dual:
            i_modes = {s["channel"]: "i" for s in sweepers if s.get("measure_current", True)}
            source_i, readings_i = self._trigger_phase(i_modes)
            for ch, src in source_i.items():
                source_vals.setdefault(ch, src)
            measured_curr.update(readings_i)

        return source_vals, measured_volt, measured_curr

    def _trigger_phase(
        self, channel_modes: dict[Any, str]
    ) -> tuple[dict[Any, float], dict[Any, float]]:
        if not channel_modes:
            return {}, {}

        channels = list(channel_modes.keys())
        for ch, mode in channel_modes.items():
            trigger_fns.set_measure_mode(ch, mode)
        trigger_fns.trigger(list(self.keithleys.values()), channels)

        source_vals: dict[Any, float] = {}
        readings: dict[Any, float] = {}
        for ch in channels:
            source_v, reading = trigger_fns.recall_buffer(ch)
            source_vals[ch] = float(source_v)
            readings[ch] = float(reading)
        return source_vals, readings

    @staticmethod
    def _set_ktime(
        sweepers: list[dict[str, Any]],
        dt_in: float,
        delay_ratio: float,
        split_for_dual: bool = False,
    ) -> None:
        for sweeper in sweepers:
            dt_effective = dt_in
            if split_for_dual and (
                sweeper.get("measure_voltage") or sweeper.get("measure_current")
            ):
                dt_effective = dt_in / 2
            elif sweeper.get("measure_voltage") and sweeper.get("measure_current"):
                dt_effective = dt_in / 2

            ch = sweeper["channel"]

            # Use the instrument-reported mains frequency (50/60 Hz) instead of
            # hard-coding 50 Hz, then compensate delay using the accepted NPLC.
            try:
                linefreq_hz = float(ch.linefreq())
                if linefreq_hz <= 0:
                    linefreq_hz = 50.0
            except Exception:
                linefreq_hz = 50.0

            nplc_target = dt_effective * linefreq_hz * (1 - delay_ratio)
            nplc_target = max(0.001, min(25.0, nplc_target))
            ch.nplc(nplc_target)

            try:
                nplc_applied = float(ch.nplc())
            except Exception:
                nplc_applied = nplc_target

            delay = max(0.0, dt_effective - (nplc_applied / linefreq_hz))
            ch.delay(delay)
