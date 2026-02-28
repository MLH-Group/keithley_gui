"""Keithley GUI package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["ArbitrarySweeperGUI", "LivePlotterGUI", "gui_main", "plotter_main"]


def __getattr__(name: str) -> Any:
    if name in ("ArbitrarySweeperGUI", "gui_main"):
        module = import_module(".gui", __name__)
    elif name in ("LivePlotterGUI", "plotter_main"):
        module = import_module(".plotter_gui", __name__)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    if name == "gui_main":
        return module.main
    if name == "plotter_main":
        return module.main
    return getattr(module, name)
