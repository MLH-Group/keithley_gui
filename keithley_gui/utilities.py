from __future__ import annotations

import logging
from time import sleep

import numpy as np
from qcodes.dataset import Measurement
from qcodes.parameters import ElapsedTimeParameter, ManualParameter

log = logging.getLogger(__name__)

COLOR_CYCLE = [
    "#264653",
    "#2A9D8F",
    "#E9C46A",
    "#F4A261",
    "#E76F51",
    "#6D597A",
    "#355070",
    "#B56576",
    "#FFB4A2",
    "#9A8C98",
]


def set_source_mode(channel, source_mode: str) -> None:
    mode = str(source_mode).strip().lower()
    if mode not in {"v", "i"}:
        mode = "v"
    mode_param = getattr(channel, "mode", None)
    if callable(mode_param):
        try:
            mode_param("current" if mode == "i" else "voltage")
        except Exception:
            pass


def ramp_source(channel, final, rampdV=5e-5, rampdT=1e-3, source_mode: str = "v"):
    mode = str(source_mode).strip().lower()
    if mode not in {"v", "i"}:
        mode = "v"
    source_param = channel.curr if mode == "i" else channel.volt
    initial = source_param()
    ramp = np.linspace(initial, final, int(1 + abs((initial - final) / rampdV)))
    log.info("ramping %s from %s to %s", channel, initial, final)
    for x in ramp:
        source_param(x)
        sleep(rampdT)


def ramp_voltage(channel, final, rampdV=5e-5, rampdT=1e-3):
    ramp_source(channel, final, rampdV=rampdV, rampdT=rampdT, source_mode="v")


def ensure_meas_v_parameter(channel):
    meas_v = getattr(channel, "meas_v", None)
    if meas_v is not None:
        return meas_v

    parameters = getattr(channel, "parameters", None)
    if isinstance(parameters, dict) and "meas_v" in parameters:
        return parameters["meas_v"]

    add_parameter = getattr(channel, "add_parameter", None)
    if callable(add_parameter):
        volt_param = getattr(channel, "volt", None)
        label = getattr(volt_param, "label", "") or f"{channel.name} measured voltage"
        unit = getattr(volt_param, "unit", "V") or "V"
        add_parameter(
            "meas_v",
            parameter_class=ManualParameter,
            label=label,
            unit=unit,
            snapshot_get=False,
        )
        meas_v = getattr(channel, "meas_v", None)
        if meas_v is not None:
            return meas_v
        parameters = getattr(channel, "parameters", None)
        if isinstance(parameters, dict) and "meas_v" in parameters:
            return parameters["meas_v"]

    fallback_name = f"{channel.name}_meas_v".replace(".", "_")
    return ManualParameter(
        name=fallback_name,
        label=f"{channel.name} measured voltage",
        unit="V",
        snapshot_get=False,
    )


def setup_database_registers_arb(
    station,
    test_exp,
    sweepers,
    time_independent=False,
    measurement_name="forward",
):
    time = ElapsedTimeParameter("time")
    meas_forward = Measurement(exp=test_exp, station=station, name=measurement_name)
    registered: set[str] = set()

    def _param_key(param) -> str:
        return str(getattr(param, "full_name", None) or getattr(param, "name", ""))

    def _register_param(param, setpoints=None, label_base: str | None = None) -> None:
        if param is None:
            return
        key = _param_key(param)
        if key in registered:
            return
        if label_base:
            param.label = label_base
        if setpoints:
            meas_forward.register_parameter(param, setpoints=setpoints)
        else:
            meas_forward.register_parameter(param)
        registered.add(key)

    independent_params = []
    for sweeper in sweepers:
        channel = sweeper["channel"]
        source_mode = str(sweeper.get("source_mode", "v")).strip().lower()
        if source_mode not in {"v", "i"}:
            source_mode = "v"
        source_param = channel.curr if source_mode == "i" else channel.volt
        if sweeper["independent"]:
            _register_param(source_param)
            if source_param not in independent_params:
                independent_params.append(source_param)

    if time_independent:
        _register_param(time)
        if time not in independent_params:
            independent_params.append(time)

    for sweeper in sweepers:
        channel = sweeper["channel"]
        channel_name = str(sweeper.get("channel_name", "")).strip()
        user_name = str(sweeper.get("name", "")).strip()
        label_base = channel_name
        if user_name:
            label_base = f"{channel_name} | {user_name}" if channel_name else user_name
        measure_current = bool(sweeper.get("measure_current", True))
        measure_voltage = bool(sweeper.get("measure_voltage", False))
        source_mode = str(sweeper.get("source_mode", "v")).strip().lower()
        if source_mode not in {"v", "i"}:
            source_mode = "v"
        source_param = channel.curr if source_mode == "i" else channel.volt
        if measure_current and source_mode != "i":
            if label_base:
                channel.curr.label = label_base
            _register_param(channel.curr, setpoints=(*independent_params,))
        if measure_voltage:
            meas_v_param = sweeper.get("meas_v_param")
            if meas_v_param is None:
                meas_v_param = ensure_meas_v_parameter(channel)
                sweeper["meas_v_param"] = meas_v_param
            if label_base:
                meas_v_param.label = label_base
            _register_param(meas_v_param, setpoints=(*independent_params,))
        if not sweeper["independent"]:
            _register_param(source_param, setpoints=(*independent_params,), label_base=label_base)

    if not time_independent:
        _register_param(time, setpoints=(*independent_params,))

    return meas_forward, time, independent_params
