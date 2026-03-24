from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PyQt5 import QtCore, QtWidgets
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure


def _to_float(value: object) -> float:
    return float(value)


def _to_int(value: object) -> int:
    return int(float(value))


@dataclass(frozen=True)
class ParamSpec:
    name: str
    default: float | int
    caster: Callable[[object], float | int]


@dataclass(frozen=True)
class MakerSpec:
    func: Callable[..., np.ndarray]
    params: tuple[ParamSpec, ...]


def hold(hold_V: float = 0.0, length: int = 100) -> np.ndarray:
    if length < 1:
        raise ValueError("length must be >= 1")
    return np.full(int(length), float(hold_V), dtype=float)


def Sinewave(
    amp: float = 1.0,
    per: int = 200,
    offset: float = 0.0,
    phase_start: float = 0.0,
    length: float = 50.0,
) -> np.ndarray:
    if per <= 0:
        raise ValueError("per must be >= 1")
    if length < 1:
        raise ValueError("length must be > 0")
    x = np.arange(int(length), dtype=float) + float(phase_start)
    return float(offset) + float(amp) * np.sin(2 * np.pi * x / float(per))


def _segment_by_step(a: float, b: float, step_size: float) -> np.ndarray:
    if step_size <= 0:
        raise ValueError("step_size must be > 0")
    if np.isclose(a, b):
        return np.array([float(a)], dtype=float)

    direction = 1.0 if b > a else -1.0
    step = direction * step_size
    vals = [float(a)]
    cur = float(a)

    while (direction > 0 and cur + step < b) or (direction < 0 and cur + step > b):
        cur += step
        vals.append(cur)

    vals.append(float(b))
    return np.array(vals, dtype=float)


def linear_segment(start: float = -0.6, end: float = 1.0, step_size: float = 0.05) -> np.ndarray:
    return _segment_by_step(float(start), float(end), float(step_size))


MAKER_SPECS: dict[str, MakerSpec] = {
    "hold": MakerSpec(
        func=hold,
        params=(
            ParamSpec("hold_V", 0.0, _to_float),
            ParamSpec("length", 100, _to_int),
        ),
    ),
    "Sinewave": MakerSpec(
        func=Sinewave,
        params=(
            ParamSpec("amp", 1.0, _to_float),
            ParamSpec("per", 200, _to_int),
            ParamSpec("offset", 0.0, _to_float),
            ParamSpec("phase_start", 0.0, _to_float),
            ParamSpec("length", 50.0, _to_float),
        ),
    ),
    "linear_segment": MakerSpec(
        func=linear_segment,
        params=(
            ParamSpec("start", -0.6, _to_float),
            ParamSpec("end", 1.0, _to_float),
            ParamSpec("step_size", 0.05, _to_float),
        ),
    ),
}


COMBINE_OPERATIONS: tuple[str, ...] = ("join", "stack", "add", "subtract", "multiply")


def make_wave(kind: str, params: dict[str, object]) -> np.ndarray:
    if kind not in MAKER_SPECS:
        raise ValueError(f"unknown maker '{kind}'")
    spec = MAKER_SPECS[kind]
    kwargs: dict[str, float | int] = {}
    for param in spec.params:
        raw = params.get(param.name, param.default)
        kwargs[param.name] = param.caster(raw)
    return np.asarray(spec.func(**kwargs), dtype=float).ravel()


def _align_pair(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = min(a.size, b.size)
    if n == 0:
        raise ValueError("both inputs must have at least one point")
    return a[:n], b[:n]


def combine_waves(a: np.ndarray, b: np.ndarray, operation: str) -> np.ndarray:
    aa = np.asarray(a, dtype=float).ravel()
    bb = np.asarray(b, dtype=float).ravel()
    if operation == "join":
        return np.concatenate([aa, bb])
    if operation == "stack":
        a2, b2 = _align_pair(aa, bb)
        return np.vstack([a2, b2])
    if operation == "add":
        a2, b2 = _align_pair(aa, bb)
        return a2 + b2
    if operation == "subtract":
        a2, b2 = _align_pair(aa, bb)
        return a2 - b2
    if operation == "multiply":
        a2, b2 = _align_pair(aa, bb)
        return a2 * b2
    raise ValueError(f"unknown operation '{operation}'")


def append_series(parts: list[np.ndarray]) -> np.ndarray:
    if not parts:
        return np.array([], dtype=float)
    return np.concatenate([np.asarray(part, dtype=float).ravel() for part in parts])


def write_csv(path: str | Path, data: np.ndarray) -> None:
    arr = np.asarray(data, dtype=float)
    out = Path(path)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if arr.ndim == 1:
            for value in arr:
                writer.writerow([value])
            return
        if arr.ndim == 2:
            for row in arr.T:
                writer.writerow(row.tolist())
            return
        raise ValueError("data must be 1D or 2D")


class MakerPanel(QtWidgets.QGroupBox):
    def __init__(self, title: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(title, parent)
        self.kind = QtWidgets.QComboBox()
        self.kind.addItems(list(MAKER_SPECS.keys()))
        self.kind.currentTextChanged.connect(self._rebuild_fields)
        self.edits: dict[str, QtWidgets.QLineEdit] = {}

        root = QtWidgets.QVBoxLayout(self)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Maker"))
        row.addWidget(self.kind, 1)
        root.addLayout(row)

        self.form_widget = QtWidgets.QWidget()
        self.form = QtWidgets.QFormLayout(self.form_widget)
        self.form.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.form_widget)

        self._rebuild_fields()

    def _rebuild_fields(self) -> None:
        while self.form.rowCount():
            self.form.removeRow(0)
        self.edits.clear()

        spec = MAKER_SPECS[self.kind.currentText()]
        for param in spec.params:
            edit = QtWidgets.QLineEdit(str(param.default))
            self.form.addRow(param.name, edit)
            self.edits[param.name] = edit

    def get_wave(self) -> np.ndarray:
        params = {name: edit.text().strip() for name, edit in self.edits.items()}
        return make_wave(self.kind.currentText(), params)


class WaveComposerDialog(QtWidgets.QDialog):
    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
        apply_to_channels_cb: Callable[[np.ndarray], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Wave Composer")
        self.resize(1200, 900)
        self.apply_to_channels_cb = apply_to_channels_cb

        self.wave_a: np.ndarray | None = None
        self.wave_b: np.ndarray | None = None
        self.result: np.ndarray | None = None
        self.series_parts: list[np.ndarray] = []

        self.operation = QtWidgets.QComboBox()
        self.operation.addItems(list(COMBINE_OPERATIONS))
        self.status = QtWidgets.QLabel("Ready")

        self._build_ui()
        self._redraw()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        splitter.setStyleSheet("QSplitter::handle{background: #c0c0c0;}")
        root.addWidget(splitter, 1)

        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls)
        top = QtWidgets.QHBoxLayout()
        self.panel_a = MakerPanel("Wave A")
        self.panel_b = MakerPanel("Wave B")
        top.addWidget(self.panel_a, 1)
        top.addWidget(self.panel_b, 1)

        ops_box = QtWidgets.QGroupBox("Combine")
        ops = QtWidgets.QVBoxLayout(ops_box)
        op_row = QtWidgets.QHBoxLayout()
        op_row.addWidget(QtWidgets.QLabel("Operation"))
        op_row.addWidget(self.operation, 1)
        ops.addLayout(op_row)

        gen_btn = QtWidgets.QPushButton("Generate + Combine")
        gen_btn.clicked.connect(self._generate_and_combine)
        ops.addWidget(gen_btn)

        append_btn = QtWidgets.QPushButton("Append Result To Series")
        append_btn.clicked.connect(self._append_result)
        ops.addWidget(append_btn)

        clear_btn = QtWidgets.QPushButton("Clear Series")
        clear_btn.clicked.connect(self._clear_series)
        ops.addWidget(clear_btn)

        save_result_btn = QtWidgets.QPushButton("Save Result CSV")
        save_result_btn.clicked.connect(self._save_result)
        ops.addWidget(save_result_btn)

        save_series_btn = QtWidgets.QPushButton("Save Series CSV")
        save_series_btn.clicked.connect(self._save_series)
        ops.addWidget(save_series_btn)

        apply_btn = QtWidgets.QPushButton("Apply Result/Series To Selected Channel(s)")
        apply_btn.clicked.connect(self._apply_result_to_channels)
        apply_btn.setEnabled(self.apply_to_channels_cb is not None)
        ops.addWidget(apply_btn)
        ops.addStretch(1)
        top.addWidget(ops_box)
        controls_layout.addLayout(top)
        controls_layout.addWidget(self.status)

        plot_widget = QtWidgets.QWidget()
        plot_layout = QtWidgets.QVBoxLayout(plot_widget)
        self.figure = Figure(figsize=(10, 8), tight_layout=True)
        self.ax_a = self.figure.add_subplot(411)
        self.ax_b = self.figure.add_subplot(412)
        self.ax_result = self.figure.add_subplot(413)
        self.ax_series = self.figure.add_subplot(414)
        self.canvas = FigureCanvasQTAgg(self.figure)
        plot_layout.addWidget(self.canvas, 1)

        splitter.addWidget(controls)
        splitter.addWidget(plot_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 580])

    def _generate_and_combine(self) -> None:
        try:
            self.wave_a = self.panel_a.get_wave()
            self.wave_b = self.panel_b.get_wave()
            self.result = combine_waves(self.wave_a, self.wave_b, self.operation.currentText())
            shape = tuple(np.asarray(self.result).shape)
            self.status.setText(
                f"Combined with '{self.operation.currentText()}'. Result shape: {shape}."
            )
            self._redraw()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Wave Composer Error", str(exc))

    def _append_result(self) -> None:
        if self.result is None:
            QtWidgets.QMessageBox.warning(self, "No Result", "Generate a result before appending.")
            return
        arr = np.asarray(self.result, dtype=float)
        if arr.ndim != 1:
            QtWidgets.QMessageBox.warning(
                self,
                "Unsupported Result",
                "Only 1D results can be appended. Use join/add/subtract/multiply.",
            )
            return
        self.series_parts.append(arr.copy())
        total = append_series(self.series_parts).size
        self.status.setText(f"Appended segment {len(self.series_parts)}. Series points: {total}.")
        self._redraw()

    def _clear_series(self) -> None:
        self.series_parts = []
        self.status.setText("Series cleared.")
        self._redraw()

    def _save_result(self) -> None:
        if self.result is None:
            QtWidgets.QMessageBox.warning(self, "No Result", "Generate a result first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Result CSV", "wavemaker_result.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        write_csv(path, np.asarray(self.result, dtype=float))
        self.status.setText(f"Saved result to {path}")

    def _save_series(self) -> None:
        series = append_series(self.series_parts)
        if series.size == 0:
            QtWidgets.QMessageBox.warning(self, "Empty Series", "Append at least one result first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Series CSV", "wavemaker_series.csv", "CSV Files (*.csv)"
        )
        if not path:
            return
        write_csv(path, series)
        self.status.setText(f"Saved series to {path}")

    def _apply_result_to_channels(self) -> None:
        if self.apply_to_channels_cb is None:
            QtWidgets.QMessageBox.warning(
                self, "Unavailable", "Apply-to-channel callback is not configured."
            )
            return
        if self.series_parts:
            arr = append_series(self.series_parts)
            if arr.size == 0:
                QtWidgets.QMessageBox.warning(self, "Empty Series", "Series has no points to apply.")
                return
        else:
            if self.result is None:
                QtWidgets.QMessageBox.warning(self, "No Result", "Generate a result first.")
                return
            arr = np.asarray(self.result, dtype=float)
            if arr.ndim != 1:
                QtWidgets.QMessageBox.warning(
                    self,
                    "Unsupported Result",
                    "Use a 1D result or append segments and apply the series.",
                )
                return
        try:
            message = self.apply_to_channels_cb(arr)
            self.status.setText(message)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Apply Failed", str(exc))

    def _plot_wave(self, ax, wave: np.ndarray | None, title: str, color: str) -> None:
        ax.clear()
        ax.set_title(title)
        ax.set_xlabel("Sample index")
        ax.set_ylabel("Value")
        ax.grid(True, alpha=0.3)
        if wave is None:
            return
        arr = np.asarray(wave, dtype=float)
        if arr.size == 0:
            return
        if arr.ndim == 1:
            ax.plot(arr, color=color, linewidth=1.1)
            return
        if arr.ndim == 2:
            for idx, row in enumerate(arr):
                ax.plot(row, linewidth=1.1, label=f"wave_{idx + 1}")
            ax.legend(loc="upper right")

    def _redraw(self) -> None:
        self._plot_wave(self.ax_a, self.wave_a, "Wave A", "tab:blue")
        self._plot_wave(self.ax_b, self.wave_b, "Wave B", "tab:orange")
        self._plot_wave(self.ax_result, self.result, "Combined Result", "tab:green")
        self._plot_wave(
            self.ax_series,
            append_series(self.series_parts),
            "Joint Series (Appended Results)",
            "tab:red",
        )
        self.figure.tight_layout()
        self.canvas.draw_idle()
