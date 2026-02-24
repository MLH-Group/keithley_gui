from __future__ import annotations

import re
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from qcodes.instrument import Instrument, InstrumentChannel
from qcodes.parameters import ManualParameter


@dataclass
class _ChannelState:
    source_levelv: float = 0.0
    source_leveli: float = 0.0
    delay: float = 0.0
    nplc: float = 1.0
    mode: int = 1
    output: int = 1
    source_rangev: float = 20.0
    source_rangei: float = 0.1
    measure_rangev: float = 20.0
    measure_rangei: float = 0.1
    measure_autozero: int = 1
    trigger_measure_mode: str = "i"
    trigger_initiated: bool = False
    pending_linear_v: float | None = None
    readings: list[float] = field(default_factory=list)
    sourcevalues: list[float] = field(default_factory=list)


class Keithley2600Channel(InstrumentChannel):
    def __init__(self, parent: Instrument, name: str, channel: str) -> None:
        if channel not in ["smua", "smub"]:
            raise ValueError('channel must be either "smub" or "smua"')

        super().__init__(parent, name)
        self.channel = channel

        self.add_parameter(
            "temperature",
            label=f"Temp{parent}{channel}",
            unit="C",
            snapshot_get=False,
        )

        self.add_parameter(
            "volt",
            label=f"Voltage{parent}{channel}",
            unit="V",
            get_cmd=f"{channel}.measure.v()",
            get_parser=float,
            set_cmd=f"{channel}.source.levelv={{}}",
            snapshot_get=False,
        )

        self.add_parameter(
            "meas_v",
            parameter_class=ManualParameter,
            label=f"MeasVoltage{parent}{channel}",
            unit="V",
            snapshot_get=False,
        )

        self.add_parameter(
            "curr",
            label=f"Current{parent}{channel}",
            unit="A",
            get_cmd=f"{channel}.measure.i()",
            get_parser=float,
            set_cmd=f"{channel}.source.leveli={{}}",
            snapshot_get=False,
        )

        self.add_parameter(
            "res",
            get_cmd=f"{channel}.measure.r()",
            get_parser=float,
            set_cmd=False,
            label="Resistance",
            unit="Ohm",
        )

        self.add_parameter(
            "delay",
            label="Delay in seconds",
            set_cmd=f"{channel}.measure.delay={{}}",
            get_cmd=f"{channel}.measure.delay",
            get_parser=float,
        )

        self.add_parameter(
            "mode",
            get_cmd=f"{channel}.source.func",
            get_parser=int,
            set_cmd=f"{channel}.source.func={{:d}}",
            val_mapping={"current": 0, "voltage": 1},
        )

        self.add_parameter(
            "output",
            get_cmd=f"{channel}.source.output",
            get_parser=int,
            set_cmd=f"{channel}.source.output={{:d}}",
            val_mapping={"on": 1, "off": 0},
        )

        self.add_parameter(
            "linefreq",
            label="Line frequency",
            get_cmd="localnode.linefreq",
            get_parser=float,
            set_cmd=False,
            unit="Hz",
        )

        self.add_parameter(
            "nplc",
            label="Number of power line cycles",
            set_cmd=f"{channel}.measure.nplc={{}}",
            get_cmd=f"{channel}.measure.nplc",
            get_parser=float,
        )

    def reset(self) -> None:
        self.write(f"{self.channel}.reset()")


class Keithley2600(Instrument):
    """
    MVP Keithley 2600 simulator for trigger-based sweeps.
    It preserves the interface shape used by this repo's notebooks.
    """
    _sim_instances: "weakref.WeakSet[Keithley2600]" = weakref.WeakSet()

    def __init__(self, name: str, address: str, **kwargs: Any) -> None:
        super().__init__(name, **kwargs)

        self.address = address
        self.model = "2614B"
        self.linefreq_hz = 50.0
        self._rng = np.random.default_rng()

        # Simple per-channel transport parameters for fake readings
        self._gain = {"smua": 2e-6, "smub": 1e-6}
        self._offset = {"smua": 0.0, "smub": 0.0}
        self._noise_i = {"smua": 5e-9, "smub": 5e-9}
        self._noise_v = {"smua": 2e-6, "smub": 2e-6}
        self.__class__._sim_instances.add(self)

        self._state: dict[str, _ChannelState] = {
            "smua": _ChannelState(),
            "smub": _ChannelState(),
        }

        self.channels: list[Keithley2600Channel] = []
        for ch in ["a", "b"]:
            ch_name = f"smu{ch}"
            channel = Keithley2600Channel(self, ch_name, ch_name)
            self.add_submodule(ch_name, channel)
            self.channels.append(channel)

    def get_idn(self) -> dict[str, str | None]:
        return {
            "vendor": "Keithley-SIM",
            "model": self.model,
            "serial": "SIM0001",
            "firmware": "sim-0.1",
        }

    def ask(self, cmd: str) -> str:
        expr = self._unwrap_print(cmd.strip())

        if expr == "localnode.model":
            return self.model
        if expr == "localnode.linefreq":
            return f"{self.linefreq_hz}"

        # Supports the status query style used in the real driver.
        m_status = re.fullmatch(
            r"(smu[ab])\.measure\.(i|v)\(\),\s*status\.measurement\.instrument\.\1\.condition",
            expr,
        )
        if m_status:
            ch, mode = m_status.group(1), m_status.group(2)
            value = self._measure_now(ch, mode)
            return f"{value}\t0.0"

        m_meas = re.fullmatch(r"(smu[ab])\.measure\.(i|v|r)\(\)", expr)
        if m_meas:
            ch, mode = m_meas.group(1), m_meas.group(2)
            if mode == "r":
                v = self._measure_now(ch, "v")
                i = self._measure_now(ch, "i")
                if abs(i) < 1e-15:
                    return "inf"
                return f"{v / i}"
            return f"{self._measure_now(ch, mode)}"

        m_reading = re.fullmatch(r"(smu[ab])\.nvbuffer1\.readings\[(\d+)\]", expr)
        if m_reading:
            ch, idx = m_reading.group(1), int(m_reading.group(2)) - 1
            return f"{self._buffer_get(self._state[ch].readings, idx)}"

        m_source = re.fullmatch(r"(smu[ab])\.nvbuffer1\.sourcevalues\[(\d+)\]", expr)
        if m_source:
            ch, idx = m_source.group(1), int(m_source.group(2)) - 1
            return f"{self._buffer_get(self._state[ch].sourcevalues, idx)}"

        m_get = re.fullmatch(
            r"(smu[ab])\.(measure\.delay|measure\.nplc|measure\.autozero|source\.func|source\.output|source\.rangev|source\.rangei|measure\.rangev|measure\.rangei)",
            expr,
        )
        if m_get:
            ch, field_name = m_get.group(1), m_get.group(2)
            state = self._state[ch]
            mapping = {
                "measure.delay": state.delay,
                "measure.nplc": state.nplc,
                "measure.autozero": state.measure_autozero,
                "source.func": state.mode,
                "source.output": state.output,
                "source.rangev": state.source_rangev,
                "source.rangei": state.source_rangei,
                "measure.rangev": state.measure_rangev,
                "measure.rangei": state.measure_rangei,
            }
            return f"{mapping[field_name]}"

        if expr == "*IDN?":
            idn = self.get_idn()
            return f"{idn['vendor']},{idn['model']},{idn['serial']},{idn['firmware']}"

        raise NotImplementedError(f"Simulator ask command not implemented: {cmd}")

    def write(self, cmd: str) -> None:
        stmt = cmd.strip()

        if stmt == "*TRG":
            self.__class__._trigger_all()
            return

        # No-op display commands used by the real driver.
        if stmt.startswith("display."):
            return

        if stmt == "reset()":
            self._state = {"smua": _ChannelState(), "smub": _ChannelState()}
            return

        m_assign = re.fullmatch(r"(smu[ab])\.([A-Za-z0-9_\.]+)\s*=\s*(.+)", stmt)
        if m_assign:
            ch, left, right = m_assign.group(1), m_assign.group(2), m_assign.group(3)
            self._handle_assignment(ch, left, right)
            return

        m_linear = re.fullmatch(
            r"(smu[ab])\.trigger\.source\.linearv\(([^,]+),\s*([^,]+),\s*([^)]+)\)",
            stmt,
        )
        if m_linear:
            ch = m_linear.group(1)
            start_v = float(m_linear.group(2))
            state = self._state[ch]
            state.pending_linear_v = start_v
            state.source_levelv = start_v
            return

        m_clear = re.fullmatch(r"(smu[ab])\.nvbuffer1\.clear\(\)", stmt)
        if m_clear:
            ch = m_clear.group(1)
            self._state[ch].readings.clear()
            self._state[ch].sourcevalues.clear()
            return

        m_trigger_init = re.fullmatch(r"(smu[ab])\.trigger\.initiate\(\)", stmt)
        if m_trigger_init:
            ch = m_trigger_init.group(1)
            self._state[ch].trigger_initiated = True
            return

        m_measure_mode = re.fullmatch(
            r"(smu[ab])\.trigger\.measure\.(i|v)\((smu[ab])\.nvbuffer1\)", stmt
        )
        if m_measure_mode:
            ch, mode = m_measure_mode.group(1), m_measure_mode.group(2)
            self._state[ch].trigger_measure_mode = mode
            return

        m_reset = re.fullmatch(r"(smu[ab])\.reset\(\)", stmt)
        if m_reset:
            ch = m_reset.group(1)
            self._state[ch] = _ChannelState()
            return

        # Supported trigger setup commands that do not affect MVP behavior.
        m_noop = re.fullmatch(
            r"(smu[ab])\.(trigger\.measure\.stimulus|trigger\.measure\.action|trigger\.source\.stimulus|trigger\.source\.action|trigger\.endsweep\.action|nvbuffer1\.appendmode|nvbuffer1\.collectsourcevalues|measure\.count|abort\(\))\s*(=.*)?",
            stmt,
        )
        if m_noop:
            return

        raise NotImplementedError(f"Simulator write command not implemented: {cmd}")

    def _handle_assignment(self, ch: str, left: str, right: str) -> None:
        state = self._state[ch]

        if left == "source.levelv":
            value = self._safe_float(right)
            state.source_levelv = value
            return
        if left == "source.leveli":
            value = self._safe_float(right)
            state.source_leveli = value
            return
        if left == "measure.delay":
            value = self._safe_float(right)
            state.delay = value
            return
        if left == "measure.nplc":
            value = self._safe_float(right)
            state.nplc = value
            return
        if left == "measure.autozero":
            value = self._safe_float(right)
            state.measure_autozero = int(round(value))
            return
        if left == "source.func":
            value = self._safe_float(right)
            state.mode = int(round(value))
            return
        if left == "source.output":
            value = self._safe_float(right)
            state.output = int(round(value))
            return
        if left == "source.rangev":
            value = self._safe_float(right)
            state.source_rangev = value
            return
        if left == "source.rangei":
            value = self._safe_float(right)
            state.source_rangei = value
            return
        if left == "measure.rangev":
            value = self._safe_float(right)
            state.measure_rangev = value
            return
        if left == "measure.rangei":
            value = self._safe_float(right)
            state.measure_rangei = value
            return

        # Ignore unsupported-but-harmless assignments in this MVP.
        if left in {
            "nvbuffer1.appendmode",
            "nvbuffer1.collectsourcevalues",
            "measure.count",
            "trigger.measure.stimulus",
            "trigger.measure.action",
            "trigger.source.stimulus",
            "trigger.source.action",
            "trigger.endsweep.action",
        }:
            return

        raise NotImplementedError(f"Simulator assignment not implemented: {ch}.{left}={right}")

    def _apply_trigger(self) -> None:
        for ch, state in self._state.items():
            if not state.trigger_initiated:
                continue

            source_v = (
                state.pending_linear_v
                if state.pending_linear_v is not None
                else state.source_levelv
            )
            state.source_levelv = source_v

            reading = self._measure_from_source(ch, source_v, state.trigger_measure_mode)
            state.readings.append(reading)
            state.sourcevalues.append(source_v)
            state.trigger_initiated = False

    @classmethod
    def _trigger_all(cls) -> None:
        # Simulate a shared trigger bus: wait once, then trigger all initiated channels.
        durations = []
        for inst in list(cls._sim_instances):
            for state in inst._state.values():
                if not state.trigger_initiated:
                    continue
                integration = (
                    state.nplc / inst.linefreq_hz if inst.linefreq_hz else 0.0
                )
                durations.append(state.delay + integration)
        if durations:
            time.sleep(max(durations))
        for inst in list(cls._sim_instances):
            inst._apply_trigger()

    def _measure_now(self, ch: str, mode: str) -> float:
        state = self._state[ch]
        source_v = state.source_levelv
        return self._measure_from_source(ch, source_v, mode)

    def _measure_from_source(self, ch: str, source_v: float, mode: str) -> float:
        if mode == "i":
            return (
                self._gain[ch] * source_v
                + self._offset[ch]
                + self._rng.normal(0.0, self._noise_i[ch])
            )
        if mode == "v":
            return source_v + self._rng.normal(0.0, self._noise_v[ch])
        raise ValueError(f"Unsupported measurement mode: {mode}")

    @staticmethod
    def _buffer_get(buffer: list[float], idx: int) -> float:
        if idx < 0 or idx >= len(buffer):
            return float("nan")
        return buffer[idx]

    @staticmethod
    def _safe_float(value: str) -> float:
        return float(value.strip().replace("\n", ""))

    @staticmethod
    def _unwrap_print(cmd: str) -> str:
        # Real driver wraps asks in print(...). Accept either form.
        if cmd.startswith("print(") and cmd.endswith(")"):
            return cmd[6:-1].strip()
        return cmd
