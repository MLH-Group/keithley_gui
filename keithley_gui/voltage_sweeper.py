from __future__ import annotations

import os
import threading
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
        sweepers.append(
            {
                "channel": channel,
                "channel_name": cfg.channel_name,
                "name": cfg.name,
                "measure_voltage": cfg.measure_voltage,
                "measure_current": cfg.measure_current,
                "first_node": cfg.first_node,
                "second_node": cfg.second_node,
                "start_voltage": cfg.start_voltage,
                "dV": cfg.dV,
                "independent": cfg.independent,
                "v_range": v_range,
            }
        )

    return sweepers


def resolve_csv_path(base: str, device: str, exp: str, run_id: int) -> str:
    if base.endswith(".csv"):
        return base
    return os.path.join(base, f"{device}{exp}_{run_id}_manual_sweep.csv")


class RunWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal()
    status = QtCore.pyqtSignal(str)
    error = QtCore.pyqtSignal(str)

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
        self.time_independent = time_independent

        self._pause_event = threading.Event()
        self._pause_event.set()
        self._rebuild_on_resume = False
        self._step_index = 0
        self.is_paused = False
        self._stop_requested = False
        self._last_volt: tuple[float, ...] | None = None

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
                for sweeper in sweepers:
                    utilities.ramp_voltage(sweeper["channel"], sweeper["v_range"][0])

            time_param.reset_clock()

            for sweeper in sweepers:
                ch = sweeper["channel"]
                trigger_fns.source_trig_params(ch)
                initial_mode = "i"
                if sweeper.get("measure_voltage") and not sweeper.get("measure_current"):
                    initial_mode = "v"
                trigger_fns.meas_trig_params(ch, initial_mode)

            plan = build_plan(self.configs, self.dt_list, self.repeat, self.round_delay)
            last_dt = None

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
                            resume_idx = find_resume_index(plan, self._last_volt)
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
                        self._step_index += 1
                        continue

                    dt_in = entry["dt"]
                    if last_dt is None or dt_in != last_dt:
                        self._set_ktime(sweepers, dt_in, self.delay_ratio)
                        last_dt = dt_in

                    for x, sweeper in zip(entry["volt"], sweepers):
                        trigger_fns.set_v(sweeper["channel"], x)

                    t = time_param()
                    get_readings = []
                    independent_params = []
                    step_source_values: dict[Any, float] = {}
                    measured_volt: dict[Any, float] = {}
                    measured_curr: dict[Any, float] = {}
                    source_vals: dict[Any, float] = {}

                    for x, sweeper in zip(entry["volt"], sweepers):
                        step_source_values[sweeper["channel"]] = float(x)

                    meas_v_sweepers = [
                        s for s in sweepers if s.get("measure_voltage")
                    ]
                    meas_i_sweepers = [
                        s for s in sweepers if s.get("measure_current")
                    ]

                    if meas_v_sweepers:
                        for sweeper in meas_v_sweepers:
                            trigger_fns.set_measure_mode(sweeper["channel"], "v")
                        trigger_fns.trigger(
                            list(self.keithleys.values()),
                            [s["channel"] for s in meas_v_sweepers],
                        )
                        for sweeper in meas_v_sweepers:
                            source_v, reading = trigger_fns.recall_buffer(
                                sweeper["channel"]
                            )
                            source_v = float(source_v)
                            measured_volt[sweeper["channel"]] = float(reading)
                            source_vals[sweeper["channel"]] = source_v

                    if meas_i_sweepers:
                        for sweeper in meas_i_sweepers:
                            trigger_fns.set_measure_mode(sweeper["channel"], "i")
                        trigger_fns.trigger(
                            list(self.keithleys.values()),
                            [s["channel"] for s in meas_i_sweepers],
                        )
                        for sweeper in meas_i_sweepers:
                            source_v, reading = trigger_fns.recall_buffer(
                                sweeper["channel"]
                            )
                            source_v = float(source_v)
                            measured_curr[sweeper["channel"]] = float(reading)
                            source_vals.setdefault(sweeper["channel"], source_v)

                    for sweeper in sweepers:
                        ch = sweeper["channel"]
                        measure_current = bool(sweeper.get("measure_current", True))
                        measure_voltage = bool(sweeper.get("measure_voltage", False))
                        source_v = source_vals.get(ch, step_source_values.get(ch, 0.0))
                        v_used = measured_volt.get(ch, source_v)
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
                            get_readings.append((ch.meas_v, v_used))

                    forward_saver.add_result(
                        *independent_params,
                        *get_readings,
                        (time_param, t),
                    )
                    self._last_volt = entry["volt"]
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
                for sweeper in sweepers:
                    utilities.ramp_voltage(sweeper["channel"], 0)

            self.finished.emit()
        except Exception as exc:
            self.error.emit(str(exc))
            self.finished.emit()

    @staticmethod
    def _set_ktime(
        sweepers: list[dict[str, Any]], dt_in: float, delay_ratio: float
    ) -> None:
        for sweeper in sweepers:
            dt_effective = dt_in
            if sweeper.get("measure_voltage") and sweeper.get("measure_current"):
                dt_effective = dt_in / 2
            nplc_set = dt_effective * 50 * (1 - delay_ratio)
            delay = dt_effective - (nplc_set / 50)
            sweeper["channel"].delay(delay)
            sweeper["channel"].nplc(nplc_set)
