from __future__ import annotations

from time import sleep

import numpy as np
from qcodes.dataset import Measurement
from qcodes.instrument.specialized_parameters import ElapsedTimeParameter


def ramp_voltage(channel, final, rampdV=5e-5, rampdT=1e-3):
    initial = channel.volt()
    ramp = np.linspace(initial, final, int(1 + abs((initial - final) / rampdV)))
    print(f"ramping {channel} from {initial} to {final}")
    for x in ramp:
        channel.volt(x)
        sleep(rampdT)


def setup_database_registers_arb(station, test_exp, sweepers, time_independent=False):
    time = ElapsedTimeParameter("time")
    meas_forward = Measurement(exp=test_exp, station=station, name="forward")

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
        measure_current = bool(sweeper.get("measure_current", True))
        measure_voltage = bool(sweeper.get("measure_voltage", False))
        if measure_current:
            meas_forward.register_parameter(channel.curr, setpoints=(*independent_params,))
        if measure_voltage:
            meas_forward.register_parameter(
                channel.meas_v, setpoints=(*independent_params,)
            )
        if not sweeper["independent"]:
            meas_forward.register_parameter(channel.volt, setpoints=(*independent_params,))

    if not time_independent:
        meas_forward.register_parameter(time, setpoints=(*independent_params,))

    return meas_forward, time, independent_params

