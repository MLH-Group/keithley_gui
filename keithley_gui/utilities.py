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


def ramp_voltage(channel, final, rampdV=5e-5, rampdT=1e-3):
    initial = channel.volt()
    ramp = np.linspace(initial, final, int(1 + abs((initial - final) / rampdV)))
    log.info("ramping %s from %s to %s", channel, initial, final)
    for x in ramp:
        channel.volt(x)
        sleep(rampdT)


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

    independent_params = []
    for sweeper in sweepers:
        channel = sweeper["channel"]
        if sweeper["independent"]:
            meas_forward.register_parameter(channel.volt)
            independent_params.append(channel.volt)

    if time_independent:
        meas_forward.register_parameter(time)
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
        if measure_current:
            if label_base:
                channel.curr.label = label_base
            meas_forward.register_parameter(channel.curr, setpoints=(*independent_params,))
        if measure_voltage:
            meas_v_param = sweeper.get("meas_v_param")
            if meas_v_param is None:
                meas_v_param = ensure_meas_v_parameter(channel)
                sweeper["meas_v_param"] = meas_v_param
            if label_base:
                meas_v_param.label = label_base
            meas_forward.register_parameter(meas_v_param, setpoints=(*independent_params,))
        if not sweeper["independent"]:
            if label_base:
                channel.volt.label = label_base
            meas_forward.register_parameter(channel.volt, setpoints=(*independent_params,))

    if not time_independent:
        meas_forward.register_parameter(time, setpoints=(*independent_params,))

    return meas_forward, time, independent_params
