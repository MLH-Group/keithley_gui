from __future__ import annotations

import re
from typing import Any


def set_measure_mode(chan: Any, mode: str) -> None:
    chan.write(f"{chan.channel}.trigger.measure.{mode}({chan.channel}.nvbuffer1)")


def meas_trig_params(chan: Any, mode: str = "i") -> None:
    # Setup buffer
    chan.write(f"{chan.channel}.measure.autozero = 1")
    set_measure_mode(chan, mode)
    chan.write(f"{chan.channel}.nvbuffer1.appendmode = 1")

    # Clear any residual values
    chan.write(f"{chan.channel}.nvbuffer1.clear()")
    chan.write(f"{chan.channel}.nvbuffer1.collectsourcevalues = 1")

    # Set measure trigger to automatic (after source)
    chan.write(f"{chan.channel}.measure.count = 1")
    chan.write(f"{chan.channel}.trigger.measure.stimulus = 0")

    # Enable
    chan.write(f"{chan.channel}.trigger.measure.action = {chan.channel}.ENABLE")


def source_trig_params(chan: Any) -> None:
    # Tie source to bus trigger
    chan.write(f"{chan.channel}.trigger.source.stimulus = trigger.EVENT_ID")

    # End-of-sweep phase action
    chan.write(f"{chan.channel}.trigger.endsweep.action = {chan.channel}.SOURCE_HOLD")

    # Enable
    chan.write(f"{chan.channel}.trigger.source.action = {chan.channel}.ENABLE")


def trigger(keithleys: list[Any], channels: list[Any]) -> None:
    for ch in channels:
        ch.write(f"{ch.channel}.nvbuffer1.clear()")
        ch.write(f"{ch.channel}.trigger.initiate()")

    trigger_insts: list[Any] = []
    seen: set[int] = set()
    for ch in channels:
        inst = getattr(ch, "root_instrument", None)
        if inst is None:
            inst = getattr(ch, "_parent", None)
        if inst is None:
            continue
        inst_id = id(inst)
        if inst_id in seen:
            continue
        seen.add(inst_id)
        trigger_insts.append(inst)
    if not trigger_insts:
        trigger_insts = keithleys

    for k in trigger_insts:
        k.write("*TRG")


def recall_buffer(ch: Any) -> tuple[str, str]:
    try:
        payload = ch.ask(
            f"{ch.channel}.nvbuffer1.sourcevalues[1], {ch.channel}.nvbuffer1.readings[1]"
        )
        parts = [p for p in re.split(r"[\t, ]+", payload.strip()) if p]
        if len(parts) >= 2:
            return parts[0], parts[1]
    except Exception:
        pass

    # Conservative fallback for firmware/transport variants that do not return both values.
    v = ch.ask(f"{ch.channel}.nvbuffer1.sourcevalues[1]")
    j = ch.ask(f"{ch.channel}.nvbuffer1.readings[1]")
    return v, j


def set_v(ch: Any, volt: float) -> None:
    volt_str = str(volt)
    ch.write(f"{ch.channel}.trigger.source.linearv({volt_str}, {volt_str}, 1)")
