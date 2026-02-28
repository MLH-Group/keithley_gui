from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from matplotlib.figure import Figure

try:
    import yaml
except Exception:  # pragma: no cover - optional dependency
    yaml = None

from qcodes.dataset import initialise_or_create_database_at
from qcodes.station import Station

from . import utilities
from .voltage_sweeper import RunWorker, build_sweepers
from .waveform_maker import ChannelConfig, build_traces


class WaveformPlot(FigureCanvasQTAgg):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        self.fig = Figure(figsize=(7, 4))
        super().__init__(self.fig)
        self.setParent(parent)
        self.ax = self.fig.add_subplot(1, 1, 1)
        self.color_cycle = [
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

    def plot(self, traces: dict[str, tuple[np.ndarray, np.ndarray]], mode: str) -> None:
        self.fig.clear()
        if not traces:
            self.ax = self.fig.add_subplot(1, 1, 1)
            self.ax.grid(True, which="both", alpha=0.3, linestyle="--", linewidth=0.6)
            self.draw()
            return

        if mode == "subplot":
            names = list(traces.keys())
            nrows, ncols = self._subplot_grid(len(names))
            for idx, name in enumerate(names, start=1):
                ax = self.fig.add_subplot(nrows, ncols, idx)
                t, v = traces[name]
                ax.set_prop_cycle(color=self.color_cycle)
                ax.plot(t, v, linestyle="-", marker="o", markersize=3, linewidth=1)
                ax.set_title(name)
                ax.set_xlabel("Time (s)")
                if (idx - 1) % ncols == 0:
                    ax.set_ylabel("Voltage (V)")
                else:
                    ax.set_ylabel("")
                ax.grid(True, which="both", alpha=0.3, linestyle="--", linewidth=0.6)
        else:
            self.ax = self.fig.add_subplot(1, 1, 1)
            self.ax.set_prop_cycle(color=self.color_cycle)
            for name, (t, v) in traces.items():
                self.ax.plot(t, v, label=name, linestyle="-", marker="o", markersize=3, linewidth=1)
            self.ax.set_xlabel("Time (s)")
            self.ax.set_ylabel("Voltage (V)")
            self.ax.legend(loc="best")
            self.ax.grid(True, which="both", alpha=0.3, linestyle="--", linewidth=0.6)
        self.fig.tight_layout()
        self.draw()

    @staticmethod
    def _subplot_grid(count: int) -> tuple[int, int]:
        if count <= 1:
            return 1, 1
        ncols = 4
        nrows = int(np.ceil(count / ncols))
        return nrows, ncols


class ArbitrarySweeperGUI(QtWidgets.QMainWindow):
    COL_CHANNEL = 0
    COL_NAME = 1
    COL_WAVEFORM = 2
    COL_MEAS_V = 3
    COL_MEAS_I = 4
    COL_LINK = 5

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Keithley Control")

        self.station: Station | None = None
        self.keithleys: dict[str, Any] = {}
        self.run_thread: QtCore.QThread | None = None
        self.run_worker: RunWorker | None = None

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        main = QtWidgets.QVBoxLayout(root)

        conn_block = self._build_connection_block()
        params_block = self._build_params_block()
        options_block = self._build_options_block()
        plot_block = self._build_plot_block()

        main.addWidget(conn_block)

        mid = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        mid.addWidget(params_block)
        mid.addWidget(options_block)
        mid.setChildrenCollapsible(False)
        mid.setStretchFactor(0, 1)
        mid.setStretchFactor(1, 0)
        mid.setSizes([850, 350])
        mid.setHandleWidth(6)
        mid.setStyleSheet("QSplitter::handle{background: #c0c0c0;}")

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(mid)
        splitter.addWidget(plot_block)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([350, 550])
        splitter.setHandleWidth(6)
        splitter.setStyleSheet("QSplitter::handle{background: #c0c0c0;}")

        main.addWidget(splitter, 1)

        self._set_defaults()
        self._load_state_on_startup()

    def _build_connection_block(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Connect / Paths")
        layout = QtWidgets.QGridLayout(box)

        self.state_path = QtWidgets.QLineEdit("gui_state.json")
        self.load_state_btn = QtWidgets.QPushButton("Load State")
        self.save_state_btn = QtWidgets.QPushButton("Save State")
        self.load_state_btn.clicked.connect(self._on_load_state)
        self.save_state_btn.clicked.connect(self._on_save_state)

        self.yaml_path = QtWidgets.QLineEdit("electrochemistry.station.sim.yaml")
        self.db_path = QtWidgets.QLineEdit("../_data/db/sim/test.db")
        self.csv_path = QtWidgets.QLineEdit("../_data/csv/sim/test.csv")
        self.exp_name = QtWidgets.QLineEdit("test")
        self.device_name = QtWidgets.QLineEdit("test")

        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self._on_connect)
        self.connect_status = QtWidgets.QLabel("Disconnected")

        self.make_db_btn = QtWidgets.QPushButton("Make DB")
        self.make_db_btn.clicked.connect(self._on_make_db)
        self.db_status = QtWidgets.QLabel("")
        self.open_plotter_btn = QtWidgets.QPushButton("Open Plotter")
        self.open_plotter_btn.clicked.connect(self._on_open_plotter)

        self.run_indicator = QtWidgets.QLabel()
        self.run_indicator.setFixedSize(12, 12)
        self._set_indicator("idle")
        self.run_status = QtWidgets.QLabel("Idle")

        state_btns = QtWidgets.QHBoxLayout()
        state_btns.addWidget(self.load_state_btn)
        state_btns.addWidget(self.save_state_btn)

        layout.addWidget(QtWidgets.QLabel("GUI state"), 0, 0)
        layout.addWidget(self.state_path, 0, 1, 1, 2)
        layout.addLayout(state_btns, 0, 3, 1, 2)

        layout.addWidget(QtWidgets.QLabel("YAML"), 1, 0)
        layout.addWidget(self.yaml_path, 1, 1, 1, 2)
        connect_row = QtWidgets.QHBoxLayout()
        connect_row.addWidget(self.connect_btn)
        connect_row.addWidget(self.connect_status)
        connect_row.addStretch(1)
        layout.addLayout(connect_row, 1, 3, 1, 2)

        layout.addWidget(QtWidgets.QLabel("DB / CSV"), 2, 0)
        layout.addWidget(self.db_path, 2, 1)
        layout.addWidget(self.csv_path, 2, 2)
        layout.addWidget(self.make_db_btn, 2, 3)
        layout.addWidget(self.db_status, 2, 4)

        layout.addWidget(QtWidgets.QLabel("Experiment / Device"), 3, 0)
        layout.addWidget(self.exp_name, 3, 1)
        layout.addWidget(self.device_name, 3, 2)

        layout.addWidget(self.open_plotter_btn, 4, 0, 1, 5)

        status_row = QtWidgets.QHBoxLayout()
        status_row.addWidget(self.run_indicator)
        status_row.addWidget(self.run_status)
        status_row.addStretch(1)
        layout.addLayout(status_row, 5, 0, 1, 5)

        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(2, 2)
        layout.setColumnStretch(4, 1)

        return box

    def _build_params_block(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Channels / Waveforms")
        layout = QtWidgets.QVBoxLayout(box)

        # Left table: compact channel list.
        self.channel_table = QtWidgets.QTableWidget(0, 6)
        self.channel_table.setHorizontalHeaderLabels(
            [
                "Channel",
                "Name",
                "Waveform",
                "Meas V",
                "Meas I",
                "Link Next",
            ]
        )
        header = self.channel_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(self.COL_CHANNEL, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_NAME, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_WAVEFORM, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COL_MEAS_V, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(self.COL_MEAS_I, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(self.COL_LINK, QtWidgets.QHeaderView.Fixed)
        self._tune_channel_table_columns()
        self.channel_table.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding
        )
        self.channel_table.selectionModel().currentRowChanged.connect(
            self._on_row_selected
        )
        self.channel_table.horizontalHeader().sectionResized.connect(
            lambda *_: self._tune_channel_table_columns()
        )

        btn_row = QtWidgets.QHBoxLayout()
        self.move_up_btn = QtWidgets.QPushButton("Move Up")
        self.move_down_btn = QtWidgets.QPushButton("Move Down")
        self.move_up_btn.clicked.connect(self._move_row_up)
        self.move_down_btn.clicked.connect(self._move_row_down)
        btn_row.addWidget(self.move_up_btn)
        btn_row.addWidget(self.move_down_btn)
        btn_row.addStretch(1)

        # Right panel: per-channel detail editor.
        self.detail_panel = self._build_detail_panel()

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.addWidget(self.channel_table)
        split.addWidget(self.detail_panel)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 0)
        split.setSizes([600, 300])
        split.setHandleWidth(6)
        split.setStyleSheet("QSplitter::handle{background: #c0c0c0;}")

        layout.addWidget(split, 1)
        layout.addLayout(btn_row)

        return box

    def _tune_channel_table_columns(self) -> None:
        header = self.channel_table.horizontalHeader()
        self.channel_table.resizeColumnsToContents()
        metrics = QtGui.QFontMetrics(self.channel_table.font())
        meas_v_text = self.channel_table.horizontalHeaderItem(self.COL_MEAS_V).text()
        meas_v_width = metrics.horizontalAdvance(meas_v_text) + 22
        meas_i_text = self.channel_table.horizontalHeaderItem(self.COL_MEAS_I).text()
        meas_i_width = metrics.horizontalAdvance(meas_i_text) + 22
        link_text = self.channel_table.horizontalHeaderItem(self.COL_LINK).text()
        link_width = metrics.horizontalAdvance(link_text) + 22
        header.resizeSection(self.COL_MEAS_V, meas_v_width)
        header.resizeSection(self.COL_MEAS_I, meas_i_width)
        header.resizeSection(self.COL_LINK, link_width)

    def _build_options_block(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Options")
        layout = QtWidgets.QGridLayout(box)

        self.ramp_up = QtWidgets.QCheckBox()
        self.ramp_down = QtWidgets.QCheckBox()
        self.dt_list = QtWidgets.QLineEdit("0.5")
        self.delayNPLC_ratio = QtWidgets.QLineEdit("0.8")
        self.repeat = QtWidgets.QLineEdit("1")
        self.round_delay = QtWidgets.QLineEdit("0")
        self.ramp_dv = QtWidgets.QLineEdit("5e-5")
        self.ramp_dt = QtWidgets.QLineEdit("1e-3")

        layout.addWidget(QtWidgets.QLabel("ramp_up"), 0, 0)
        layout.addWidget(self.ramp_up, 0, 1)
        layout.addWidget(QtWidgets.QLabel("ramp_down"), 0, 2)
        layout.addWidget(self.ramp_down, 0, 3)

        layout.addWidget(QtWidgets.QLabel("ramp_dV"), 1, 0)
        layout.addWidget(self.ramp_dv, 1, 1)
        layout.addWidget(QtWidgets.QLabel("ramp_dT (s)"), 1, 2)
        layout.addWidget(self.ramp_dt, 1, 3)

        layout.addWidget(QtWidgets.QLabel("dt_list (comma)"), 2, 0)
        layout.addWidget(self.dt_list, 2, 1)
        layout.addWidget(QtWidgets.QLabel("delayNPLC_ratio"), 2, 2)
        layout.addWidget(self.delayNPLC_ratio, 2, 3)

        layout.addWidget(QtWidgets.QLabel("repeat"), 3, 0)
        layout.addWidget(self.repeat, 3, 1)
        layout.addWidget(QtWidgets.QLabel("round_delay (s)"), 3, 2)
        layout.addWidget(self.round_delay, 3, 3)

        return box

    def _build_plot_block(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Waveforms vs Time")
        layout = QtWidgets.QHBoxLayout(box)

        control_col = QtWidgets.QVBoxLayout()
        self.overlay_radio = QtWidgets.QRadioButton("Overlay")
        self.subplot_radio = QtWidgets.QRadioButton("Subplots")
        self.overlay_radio.setChecked(True)
        control_col.addWidget(self.overlay_radio)
        control_col.addWidget(self.subplot_radio)

        self.plot_btn = QtWidgets.QPushButton("Plot Waveforms")
        self.plot_btn.clicked.connect(self._on_plot)
        control_col.addWidget(self.plot_btn)

        control_col.addWidget(QtWidgets.QFrame(frameShape=QtWidgets.QFrame.HLine))

        self.run_btn = QtWidgets.QPushButton("Run")
        self.run_btn.clicked.connect(self._on_run)
        control_col.addWidget(self.run_btn)

        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        control_col.addWidget(self.stop_btn)

        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setEnabled(False)
        self.pause_btn.clicked.connect(self._on_pause_resume)
        control_col.addWidget(self.pause_btn)

        self.ramp_to_zero_btn = QtWidgets.QPushButton("Ramp Channels To 0")
        self.ramp_to_zero_btn.clicked.connect(self._on_ramp_to_zero)
        control_col.addWidget(self.ramp_to_zero_btn)

        control_col.addStretch(1)
        layout.addLayout(control_col)
        self.plot = WaveformPlot()
        layout.addWidget(self.plot, 1)
        layout.setStretch(0, 0)
        layout.setStretch(1, 1)
        return box

    def _set_defaults(self) -> None:
        self.ramp_up.setChecked(True)
        self.ramp_down.setChecked(False)

    def _load_state_on_startup(self) -> None:
        path = self._state_path()
        if not path or not os.path.isfile(path):
            return
        self._load_state_from_path(path, show_errors=False)

    def _state_path(self) -> str:
        raw = self.state_path.text().strip()
        if not raw:
            return ""
        return os.path.expandvars(os.path.expanduser(raw))

    def _on_save_state(self) -> None:
        self._on_apply_details()
        path = self._state_path()
        if not path:
            QtWidgets.QMessageBox.warning(self, "Missing State File", "State file name is required.")
            return
        state = self._collect_gui_state()
        try:
            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, sort_keys=True)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Save Failed", str(exc))
            return
        QtWidgets.QMessageBox.information(self, "State Saved", f"Saved GUI state to:\n{path}")

    def _on_load_state(self) -> None:
        path = self._state_path()
        if not path:
            QtWidgets.QMessageBox.warning(self, "Missing State File", "State file name is required.")
            return
        self._load_state_from_path(path, show_errors=True)

    def _load_state_from_path(self, path: str, show_errors: bool) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except FileNotFoundError:
            if show_errors:
                QtWidgets.QMessageBox.warning(self, "State File Missing", f"Could not find:\n{path}")
            return
        except Exception as exc:
            if show_errors:
                QtWidgets.QMessageBox.critical(self, "Load Failed", str(exc))
            return
        self._apply_gui_state(state)
        if show_errors:
            QtWidgets.QMessageBox.information(self, "State Loaded", f"Loaded GUI state from:\n{path}")

    def _collect_gui_state(self) -> dict[str, Any]:
        channels: list[dict[str, Any]] = []
        for row in range(self.channel_table.rowCount()):
            channel_name = self._get_table_text(row, self.COL_CHANNEL, f"row{row}")
            name = self._get_table_text(row, self.COL_NAME, channel_name)
            waveform = self._get_waveform_value(row)
            measure_voltage = self._get_check_state(row, self.COL_MEAS_V)
            measure_current = self._get_check_state(row, self.COL_MEAS_I)
            link_next = self._get_check_state(row, self.COL_LINK)
            state = dict(self._get_row_state(row))
            state["channel_name"] = channel_name
            state["waveform"] = waveform
            channels.append(
                {
                    "channel_name": channel_name,
                    "name": name,
                    "waveform": waveform,
                    "measure_voltage": measure_voltage,
                    "measure_current": measure_current,
                    "link_next": link_next,
                    "state": state,
                }
            )

        return {
            "version": 1,
            "paths": {
                "yaml_path": self.yaml_path.text(),
                "db_path": self.db_path.text(),
                "csv_path": self.csv_path.text(),
                "exp_name": self.exp_name.text(),
                "device_name": self.device_name.text(),
            },
            "options": {
                "ramp_up": self.ramp_up.isChecked(),
                "ramp_down": self.ramp_down.isChecked(),
                "dt_list": self.dt_list.text(),
                "delayNPLC_ratio": self.delayNPLC_ratio.text(),
                "repeat": self.repeat.text(),
                "round_delay": self.round_delay.text(),
                "ramp_dv": self.ramp_dv.text(),
                "ramp_dt": self.ramp_dt.text(),
                "waveform_layout": "subplot" if self.subplot_radio.isChecked() else "overlay",
            },
            "channels": channels,
        }

    def _apply_gui_state(self, state: dict[str, Any]) -> None:
        paths = state.get("paths", {})
        if isinstance(paths, dict):
            self.yaml_path.setText(paths.get("yaml_path", self.yaml_path.text()))
            self.db_path.setText(paths.get("db_path", self.db_path.text()))
            self.csv_path.setText(paths.get("csv_path", self.csv_path.text()))
            self.exp_name.setText(paths.get("exp_name", self.exp_name.text()))
            self.device_name.setText(paths.get("device_name", self.device_name.text()))

        options = state.get("options", {})
        if isinstance(options, dict):
            self.ramp_up.setChecked(bool(options.get("ramp_up", self.ramp_up.isChecked())))
            self.ramp_down.setChecked(bool(options.get("ramp_down", self.ramp_down.isChecked())))
            self.dt_list.setText(str(options.get("dt_list", self.dt_list.text())))
            self.delayNPLC_ratio.setText(str(options.get("delayNPLC_ratio", self.delayNPLC_ratio.text())))
            self.repeat.setText(str(options.get("repeat", self.repeat.text())))
            self.round_delay.setText(str(options.get("round_delay", self.round_delay.text())))
            self.ramp_dv.setText(str(options.get("ramp_dv", self.ramp_dv.text())))
            self.ramp_dt.setText(str(options.get("ramp_dt", self.ramp_dt.text())))
            layout = str(options.get("waveform_layout", "overlay"))
            if layout == "subplot":
                self.subplot_radio.setChecked(True)
            else:
                self.overlay_radio.setChecked(True)

        channels = state.get("channels")
        if isinstance(channels, list):
            self.channel_table.setRowCount(0)
            for row_state in channels:
                if isinstance(row_state, dict):
                    self._add_channel_row_from_state(row_state)
            if self.channel_table.rowCount() > 0:
                self.channel_table.selectRow(0)
                self._load_details_from_row(0)

    def _add_channel_row_from_state(self, data: dict[str, Any]) -> None:
        row = self.channel_table.rowCount()
        self.channel_table.insertRow(row)

        channel_name = str(data.get("channel_name", f"row{row}"))
        name = str(data.get("name", channel_name))
        waveform = str(data.get("waveform", "Triangle"))
        if waveform not in {"Triangle", "Square", "Square-3", "Sine", "Fixed"}:
            waveform = "Triangle"

        self.channel_table.setItem(row, self.COL_CHANNEL, QtWidgets.QTableWidgetItem(channel_name))
        self.channel_table.setItem(row, self.COL_NAME, QtWidgets.QTableWidgetItem(name))

        combo = QtWidgets.QComboBox()
        combo.addItems(["Triangle", "Square", "Square-3", "Sine", "Fixed", "CSV"])
        combo.setCurrentText(waveform)
        combo.setProperty("row", row)
        combo.currentTextChanged.connect(self._on_waveform_changed_for_widget)
        self.channel_table.setCellWidget(row, self.COL_WAVEFORM, combo)

        measure_voltage = data.get("measure_voltage")
        measure_current = data.get("measure_current")
        if measure_voltage is None:
            measure_voltage = False
        if measure_current is None:
            measure_current = True

        meas_v_item = QtWidgets.QTableWidgetItem()
        meas_v_item.setFlags(meas_v_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        meas_v_item.setCheckState(
            QtCore.Qt.Checked if measure_voltage else QtCore.Qt.Unchecked
        )
        self.channel_table.setItem(row, self.COL_MEAS_V, meas_v_item)

        meas_i_item = QtWidgets.QTableWidgetItem()
        meas_i_item.setFlags(meas_i_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        meas_i_item.setCheckState(
            QtCore.Qt.Checked if measure_current else QtCore.Qt.Unchecked
        )
        self.channel_table.setItem(row, self.COL_MEAS_I, meas_i_item)

        link_next = QtWidgets.QTableWidgetItem()
        link_next.setFlags(link_next.flags() | QtCore.Qt.ItemIsUserCheckable)
        link_next.setCheckState(QtCore.Qt.Checked if data.get("link_next") else QtCore.Qt.Unchecked)
        self.channel_table.setItem(row, self.COL_LINK, link_next)

        state = data.get("state")
        if not isinstance(state, dict):
            state = self._default_row_state(channel_name)
        state = dict(state)
        state["channel_name"] = channel_name
        state["waveform"] = waveform
        self._set_row_state(row, state)

    def _get_table_text(self, row: int, col: int, default: str) -> str:
        item = self.channel_table.item(row, col)
        if item is None:
            return default
        return item.text().strip() or default

    def _get_check_state(self, row: int, col: int) -> bool:
        item = self.channel_table.item(row, col)
        if item is None:
            return False
        return item.checkState() == QtCore.Qt.Checked

    def _on_connect(self) -> None:
        yaml_path = self.yaml_path.text().strip()
        if not yaml_path:
            self.connect_status.setText("YAML path required")
            return

        try:
            self.station = Station(config_file=yaml_path)
            instruments = self._load_yaml_instruments(yaml_path)
            self.keithleys.clear()
            for name in instruments:
                if name.startswith("keithley"):
                    loader = getattr(self.station, f"load_{name}", None)
                    if loader is None:
                        continue
                    self.keithleys[name] = loader()

            if not self.keithleys:
                self.connect_status.setText("No keithleys found")
                return

            self.connect_status.setText("Connected")
            if self.channel_table.rowCount() == 0:
                self._populate_channels()
        except Exception as exc:
            self.connect_status.setText(f"Connect failed: {exc}")

    def _on_make_db(self) -> None:
        db_path = self.db_path.text().strip()
        if not db_path:
            self.db_status.setText("DB path required")
            return
        try:
            initialise_or_create_database_at(db_path)
            self.db_status.setText("DB ready")
        except Exception as exc:
            self.db_status.setText(f"DB error: {exc}")

    def _on_open_plotter(self) -> None:
        db_path_raw = self.db_path.text().strip()
        if not db_path_raw:
            QtWidgets.QMessageBox.warning(self, "Missing DB Path", "DB path is required.")
            return
        db_path = os.path.abspath(os.path.expanduser(os.path.expandvars(db_path_raw)))
        if not os.path.isfile(db_path):
            QtWidgets.QMessageBox.warning(self, "Missing File", f"Could not find:\n{db_path}")
            return
        script_path = os.path.join(os.path.dirname(__file__), "k_plotter.py")
        if not os.path.isfile(script_path):
            QtWidgets.QMessageBox.warning(self, "Missing Plotter", f"Could not find:\n{script_path}")
            return
        try:
            subprocess.Popen(
                [sys.executable, script_path, "--db", db_path],
                cwd=os.path.dirname(script_path),
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Failed To Open Plotter", str(exc))

    def _populate_channels(self) -> None:
        self.channel_table.setRowCount(0)
        for kname, inst in self.keithleys.items():
            for ch in ["smua", "smub"]:
                self._add_channel_row(f"{kname}.{ch}")

    def _add_channel_row(self, channel_name: str) -> None:
        row = self.channel_table.rowCount()
        self.channel_table.insertRow(row)

        self.channel_table.setItem(
            row, self.COL_CHANNEL, QtWidgets.QTableWidgetItem(channel_name)
        )
        self.channel_table.setItem(
            row, self.COL_NAME, QtWidgets.QTableWidgetItem(channel_name)
        )

        combo = QtWidgets.QComboBox()
        combo.addItems(["Triangle", "Square", "Square-3", "Sine", "Fixed", "CSV"])
        combo.setCurrentText("Triangle")
        combo.setProperty("row", row)
        combo.currentTextChanged.connect(self._on_waveform_changed_for_widget)
        self.channel_table.setCellWidget(row, self.COL_WAVEFORM, combo)

        meas_v_item = QtWidgets.QTableWidgetItem()
        meas_v_item.setFlags(meas_v_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        meas_v_item.setCheckState(QtCore.Qt.Unchecked)
        self.channel_table.setItem(row, self.COL_MEAS_V, meas_v_item)

        meas_i_item = QtWidgets.QTableWidgetItem()
        meas_i_item.setFlags(meas_i_item.flags() | QtCore.Qt.ItemIsUserCheckable)
        meas_i_item.setCheckState(QtCore.Qt.Checked)
        self.channel_table.setItem(row, self.COL_MEAS_I, meas_i_item)

        link_next = QtWidgets.QTableWidgetItem()
        link_next.setFlags(link_next.flags() | QtCore.Qt.ItemIsUserCheckable)
        link_next.setCheckState(QtCore.Qt.Unchecked)
        self.channel_table.setItem(row, self.COL_LINK, link_next)

        # Initialize per-row detail state.
        self._set_row_state_defaults(row)
        if row == 0:
            self.channel_table.selectRow(0)
            self._load_details_from_row(0)

    def _move_row_up(self) -> None:
        row = self.channel_table.currentRow()
        if row <= 0:
            return
        self._swap_rows(row, row - 1)
        self.channel_table.selectRow(row - 1)

    def _move_row_down(self) -> None:
        row = self.channel_table.currentRow()
        if row < 0 or row >= self.channel_table.rowCount() - 1:
            return
        self._swap_rows(row, row + 1)
        self.channel_table.selectRow(row + 1)

    def _swap_rows(self, a: int, b: int) -> None:
        row_a = self._get_row_data(a)
        row_b = self._get_row_data(b)
        self._set_row_data(a, row_b)
        self._set_row_data(b, row_a)

    def _on_plot(self) -> None:
        try:
            configs = self._collect_channel_configs()
            dt_list = self._parse_float_list(self.dt_list.text())
            repeat = int(self.repeat.text().strip() or "1")
            round_delay = float(self.round_delay.text().strip() or "0")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid Input", str(exc))
            return

        traces = build_traces(configs, dt_list, repeat, round_delay)
        mode = "subplot" if self.subplot_radio.isChecked() else "overlay"
        self.plot.plot(traces, mode)

    def _collect_channel_configs(self) -> list[ChannelConfig]:
        configs: list[ChannelConfig] = []
        for row in range(self.channel_table.rowCount()):
            channel_name = self.channel_table.item(
                row, self.COL_CHANNEL
            ).text().strip()
            name = self.channel_table.item(row, self.COL_NAME).text().strip()
            waveform = self._get_waveform_value(row)

            state = self._get_row_state(row)
            start_voltage = float(state["start_voltage"])
            first_node = float(state["first_node"])
            second_node = float(state["second_node"])
            dV = float(state["dV"])
            v_high = float(state["v_high"])
            v_low = float(state["v_low"])
            n_high = int(state["n_high"])
            n_low = int(state["n_low"])
            n_ramp = int(state["n_ramp"])
            n_offset = int(state["n_offset"])
            v_mid = float(state["v_mid"])
            v_fixed = float(state["v_fixed"])
            n_mid = int(state["n_mid"])
            v_amp = float(state["v_amp"])
            v_offset = float(state["v_offset"])
            n_period = int(state["n_period"])
            csv_path = str(state.get("csv_path", "")).strip()

            link_next = (
                self.channel_table.item(row, self.COL_LINK).checkState() == QtCore.Qt.Checked
            )
            measure_voltage = (
                self.channel_table.item(row, self.COL_MEAS_V).checkState()
                == QtCore.Qt.Checked
            )
            measure_current = (
                self.channel_table.item(row, self.COL_MEAS_I).checkState()
                == QtCore.Qt.Checked
            )

            configs.append(
                ChannelConfig(
                    channel_name=channel_name,
                    name=name,
                    waveform=waveform,
                    measure_voltage=measure_voltage,
                    measure_current=measure_current,
                    start_voltage=start_voltage,
                    first_node=first_node,
                    second_node=second_node,
                    dV=dV,
                    v_high=v_high,
                    v_low=v_low,
                    v_mid=v_mid,
                    v_fixed=v_fixed,
                    n_high=n_high,
                    n_low=n_low,
                    n_mid=n_mid,
                    n_ramp=n_ramp,
                    n_offset=n_offset,
                    v_amp=v_amp,
                    v_offset=v_offset,
                    n_period=n_period,
                    csv_path=csv_path,
                    independent=False,
                    link_next=link_next,
                )
            )
        return configs

    @staticmethod
    def _parse_float_list(text: str) -> list[float]:
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if not parts:
            raise ValueError("dt_list is empty")
        return [float(p) for p in parts]

    def _get_waveform_value(self, row: int) -> str:
        widget = self.channel_table.cellWidget(row, self.COL_WAVEFORM)
        if isinstance(widget, QtWidgets.QComboBox):
            return widget.currentText()
        return "Triangle"

    def _on_waveform_changed_for_widget(self, _value: str) -> None:
        combo = self.sender()
        if not isinstance(combo, QtWidgets.QComboBox):
            return
        row = combo.property("row")
        if row is None:
            return
        if self.channel_table.currentRow() == int(row):
            self._update_detail_visibility(combo.currentText())

    def _get_row_data(self, row: int) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for col in range(self.channel_table.columnCount()):
            widget = self.channel_table.cellWidget(row, col)
            if isinstance(widget, QtWidgets.QComboBox):
                data[col] = ("combo", widget.currentText())
            else:
                item = self.channel_table.item(row, col)
                if item is None:
                    data[col] = ("item", "", QtCore.Qt.Unchecked)
                else:
                    data[col] = ("item", item.text(), item.checkState())
        state_item = self.channel_table.item(row, self.COL_CHANNEL)
        if state_item is not None:
            data["__state__"] = state_item.data(QtCore.Qt.UserRole)
        return data

    def _set_row_data(self, row: int, data: dict[str, Any]) -> None:
        for col, value in data.items():
            if col == "__state__":
                continue
            if value[0] == "combo":
                combo = QtWidgets.QComboBox()
                combo.addItems(["Triangle", "Square", "Square-3", "Sine", "Fixed", "CSV"])
                combo.setCurrentText(value[1])
                combo.setProperty("row", row)
                combo.currentTextChanged.connect(self._on_waveform_changed_for_widget)
                self.channel_table.setCellWidget(row, col, combo)
            else:
                text = value[1]
                check = value[2]
                item = QtWidgets.QTableWidgetItem(text)
                if col in (self.COL_LINK, self.COL_MEAS_V, self.COL_MEAS_I):
                    item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
                    item.setCheckState(check)
                self.channel_table.setItem(row, col, item)

        if "__state__" in data:
            state_item = self.channel_table.item(row, self.COL_CHANNEL)
            if state_item is not None:
                state_item.setData(QtCore.Qt.UserRole, data["__state__"])

        if row == self.channel_table.currentRow():
            self._load_details_from_row(row)

    def _build_detail_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        self.detail_title = QtWidgets.QLabel("Channel Details")
        layout.addWidget(self.detail_title)

        # Triangle params
        self.tri_group = QtWidgets.QGroupBox("Triangle Params")
        tri_layout = QtWidgets.QFormLayout(self.tri_group)
        self.tri_start = QtWidgets.QLineEdit("0.0")
        self.tri_first = QtWidgets.QLineEdit("0.0")
        self.tri_second = QtWidgets.QLineEdit("0.0")
        self.tri_dv = QtWidgets.QLineEdit("0.0")
        tri_layout.addRow("Start V", self.tri_start)
        tri_layout.addRow("First Node", self.tri_first)
        tri_layout.addRow("Second Node", self.tri_second)
        tri_layout.addRow("dV", self.tri_dv)

        # Square params
        self.square_group = QtWidgets.QGroupBox("Square Params")
        sq_layout = QtWidgets.QFormLayout(self.square_group)
        self.sq_v_high = QtWidgets.QLineEdit("0.0")
        self.sq_v_low = QtWidgets.QLineEdit("0.0")
        self.sq_n_high = QtWidgets.QLineEdit("10")
        self.sq_n_low = QtWidgets.QLineEdit("10")
        self.sq_n_ramp = QtWidgets.QLineEdit("0")
        self.sq_n_offset = QtWidgets.QLineEdit("0")
        sq_layout.addRow("V High", self.sq_v_high)
        sq_layout.addRow("V Low", self.sq_v_low)
        sq_layout.addRow("n_high", self.sq_n_high)
        sq_layout.addRow("n_low", self.sq_n_low)
        sq_layout.addRow("n_ramp", self.sq_n_ramp)
        sq_layout.addRow("n_offset", self.sq_n_offset)

        # Three-stage square params
        self.square3_group = QtWidgets.QGroupBox("Square-3 Params")
        sq3_layout = QtWidgets.QFormLayout(self.square3_group)
        self.sq3_v_high = QtWidgets.QLineEdit("0.0")
        self.sq3_v_low = QtWidgets.QLineEdit("0.0")
        self.sq3_v_mid = QtWidgets.QLineEdit("0.0")
        self.sq3_n_high = QtWidgets.QLineEdit("10")
        self.sq3_n_low = QtWidgets.QLineEdit("10")
        self.sq3_n_mid = QtWidgets.QLineEdit("10")
        self.sq3_n_offset = QtWidgets.QLineEdit("0")
        sq3_layout.addRow("V High", self.sq3_v_high)
        sq3_layout.addRow("V Low", self.sq3_v_low)
        sq3_layout.addRow("V Mid", self.sq3_v_mid)
        sq3_layout.addRow("n_high", self.sq3_n_high)
        sq3_layout.addRow("n_low", self.sq3_n_low)
        sq3_layout.addRow("n_mid", self.sq3_n_mid)
        sq3_layout.addRow("n_offset", self.sq3_n_offset)

        # Sine params
        self.sine_group = QtWidgets.QGroupBox("Sine Params")
        sine_layout = QtWidgets.QFormLayout(self.sine_group)
        self.sine_v_amp = QtWidgets.QLineEdit("0.0")
        self.sine_v_offset = QtWidgets.QLineEdit("0.0")
        self.sine_n_period = QtWidgets.QLineEdit("100")
        sine_layout.addRow("V Amp", self.sine_v_amp)
        sine_layout.addRow("V Offset", self.sine_v_offset)
        sine_layout.addRow("n_period", self.sine_n_period)

        # Fixed params
        self.fixed_group = QtWidgets.QGroupBox("Fixed Params")
        fixed_layout = QtWidgets.QFormLayout(self.fixed_group)
        self.fixed_v = QtWidgets.QLineEdit("0.0")
        fixed_layout.addRow("V Fixed", self.fixed_v)

        # CSV params
        self.csv_group = QtWidgets.QGroupBox("CSV Params")
        csv_layout = QtWidgets.QHBoxLayout(self.csv_group)
        self.csv_path = QtWidgets.QLineEdit("")
        self.csv_browse_btn = QtWidgets.QPushButton("Browse")
        self.csv_browse_btn.clicked.connect(self._on_browse_csv)
        csv_layout.addWidget(self.csv_path, 1)
        csv_layout.addWidget(self.csv_browse_btn)

        layout.addWidget(self.tri_group)
        layout.addWidget(self.square_group)
        layout.addWidget(self.square3_group)
        layout.addWidget(self.sine_group)
        layout.addWidget(self.fixed_group)
        layout.addWidget(self.csv_group)

        self.save_detail_btn = QtWidgets.QPushButton("Apply To Selected Channel")
        self.save_detail_btn.clicked.connect(self._on_apply_details)
        layout.addWidget(self.save_detail_btn)
        layout.addStretch(1)

        self._update_detail_visibility("Triangle")
        return panel

    def _on_row_selected(self, current: QtCore.QModelIndex, _prev: QtCore.QModelIndex) -> None:
        if not current.isValid():
            return
        self._load_details_from_row(current.row())

    def _load_details_from_row(self, row: int) -> None:
        state = self._get_row_state(row)
        self.detail_title.setText(f"Channel Details: {state['channel_name']}")
        waveform = state["waveform"]
        self._update_detail_visibility(waveform)

        self.tri_start.setText(str(state["start_voltage"]))
        self.tri_first.setText(str(state["first_node"]))
        self.tri_second.setText(str(state["second_node"]))
        self.tri_dv.setText(str(state["dV"]))

        self.sq_v_high.setText(str(state["v_high"]))
        self.sq_v_low.setText(str(state["v_low"]))
        self.sq_n_high.setText(str(state["n_high"]))
        self.sq_n_low.setText(str(state["n_low"]))
        self.sq_n_ramp.setText(str(state["n_ramp"]))
        self.sq_n_offset.setText(str(state["n_offset"]))

        self.sq3_v_high.setText(str(state["v_high"]))
        self.sq3_v_low.setText(str(state["v_low"]))
        self.sq3_v_mid.setText(str(state["v_mid"]))
        self.sq3_n_high.setText(str(state["n_high"]))
        self.sq3_n_low.setText(str(state["n_low"]))
        self.sq3_n_mid.setText(str(state["n_mid"]))
        self.sq3_n_offset.setText(str(state["n_offset"]))

        self.sine_v_amp.setText(str(state["v_amp"]))
        self.sine_v_offset.setText(str(state["v_offset"]))
        self.sine_n_period.setText(str(state["n_period"]))

        self.fixed_v.setText(str(state["v_fixed"]))
        self.csv_path.setText(str(state.get("csv_path", "")))

    def _on_apply_details(self) -> None:
        row = self.channel_table.currentRow()
        if row < 0:
            return
        state = self._get_row_state(row)
        waveform = self._get_waveform_value(row)
        state["waveform"] = waveform

        state["start_voltage"] = self.tri_start.text()
        state["first_node"] = self.tri_first.text()
        state["second_node"] = self.tri_second.text()
        state["dV"] = self.tri_dv.text()

        if waveform.lower() == "square-3":
            state["v_high"] = self.sq3_v_high.text()
            state["v_low"] = self.sq3_v_low.text()
            state["n_high"] = self.sq3_n_high.text()
            state["n_low"] = self.sq3_n_low.text()
            state["n_offset"] = self.sq3_n_offset.text()
        else:
            state["v_high"] = self.sq_v_high.text()
            state["v_low"] = self.sq_v_low.text()
            state["n_high"] = self.sq_n_high.text()
            state["n_low"] = self.sq_n_low.text()
            state["n_offset"] = self.sq_n_offset.text()

        state["n_ramp"] = self.sq_n_ramp.text()

        state["v_mid"] = self.sq3_v_mid.text()
        state["n_mid"] = self.sq3_n_mid.text()

        state["v_amp"] = self.sine_v_amp.text()
        state["v_offset"] = self.sine_v_offset.text()
        state["n_period"] = self.sine_n_period.text()

        state["v_fixed"] = self.fixed_v.text()
        state["csv_path"] = self.csv_path.text().strip()

        self._set_row_state(row, state)

    def _on_browse_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select CSV Waveform", "", "CSV Files (*.csv);;All Files (*)"
        )
        if path:
            self.csv_path.setText(path)

    def _update_detail_visibility(self, waveform: str) -> None:
        wf = waveform.lower()
        self.tri_group.setVisible(wf == "triangle")
        self.square_group.setVisible(wf == "square")
        self.square3_group.setVisible(wf == "square-3")
        self.sine_group.setVisible(wf == "sine")
        self.fixed_group.setVisible(wf == "fixed")
        self.csv_group.setVisible(wf == "csv")

    def _get_row_state(self, row: int) -> dict[str, Any]:
        # Persist per-row detail state on the row item itself.
        item = self.channel_table.item(row, self.COL_CHANNEL)
        if item is None:
            return self._default_row_state(f"row{row}")
        state = item.data(QtCore.Qt.UserRole)
        if not isinstance(state, dict):
            state = self._default_row_state(item.text())
            item.setData(QtCore.Qt.UserRole, state)
        state["channel_name"] = item.text()
        state["waveform"] = self._get_waveform_value(row)
        return state

    def _set_row_state(self, row: int, state: dict[str, Any]) -> None:
        item = self.channel_table.item(row, self.COL_CHANNEL)
        if item is None:
            item = QtWidgets.QTableWidgetItem(state.get("channel_name", f"row{row}"))
            self.channel_table.setItem(row, self.COL_CHANNEL, item)
        item.setData(QtCore.Qt.UserRole, state)

    @staticmethod
    def _default_row_state(channel_name: str) -> dict[str, Any]:
        return {
            "channel_name": channel_name,
            "waveform": "Triangle",
            "start_voltage": "0.0",
            "first_node": "0.0",
            "second_node": "0.0",
            "dV": "0.0",
            "v_high": "0.0",
            "v_low": "0.0",
            "v_mid": "0.0",
            "v_fixed": "0.0",
            "n_high": "10",
            "n_low": "10",
            "n_mid": "10",
            "n_ramp": "0",
            "n_offset": "0",
            "v_amp": "0.0",
            "v_offset": "0.0",
            "n_period": "100",
            "csv_path": "",
        }

    def _set_row_state_defaults(self, row: int) -> None:
        item = self.channel_table.item(row, self.COL_CHANNEL)
        if item is None:
            return
        item.setData(QtCore.Qt.UserRole, self._default_row_state(item.text()))

    def _set_indicator(self, state: str) -> None:
        if state == "running":
            color = "#2ecc71"
        elif state == "paused":
            color = "#f1c40f"
        else:
            color = "#95a5a6"
        self.run_indicator.setStyleSheet(
            f"background-color: {color}; border-radius: 6px;"
        )

    def _set_run_state(self, running: bool, paused: bool = False) -> None:
        self.run_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running)
        self.stop_btn.setEnabled(running)
        if not running:
            self.run_status.setText("Idle")
            self._set_indicator("idle")
            self.pause_btn.setText("Pause")
        elif paused:
            self.run_status.setText("Paused")
            self._set_indicator("paused")
            self.pause_btn.setText("Resume")
        else:
            self.run_status.setText("Running")
            self._set_indicator("running")
            self.pause_btn.setText("Pause")

    def _on_pause_resume(self) -> None:
        if self.run_worker is None:
            return
        if self.run_worker.is_paused:
            try:
                configs = self._collect_channel_configs()
                dt_list = self._parse_float_list(self.dt_list.text())
                repeat = int(self.repeat.text().strip() or "1")
                round_delay = float(self.round_delay.text().strip() or "0")
                delay_ratio = float(self.delayNPLC_ratio.text().strip() or "0.8")
            except Exception as exc:
                QtWidgets.QMessageBox.warning(self, "Invalid Input", str(exc))
                return
            self.run_worker.request_resume(configs, dt_list, repeat, round_delay, delay_ratio)
            self._set_run_state(True, paused=False)
        else:
            self.run_worker.request_pause()
            self._set_run_state(True, paused=True)

    def _on_stop(self) -> None:
        if self.run_worker is None:
            return
        self.run_worker.request_stop()
        self._set_run_state(False)

    def _on_ramp_to_zero(self) -> None:
        if self.station is None or not self.keithleys:
            QtWidgets.QMessageBox.warning(self, "Not Connected", "Connect to keithleys first.")
            return
        try:
            configs = self._collect_channel_configs()
            sweepers, _ = build_sweepers(
                configs, self.keithleys, square_final_low=False
            )
            ramp_dv = float(self.ramp_dv.text().strip() or "5e-5")
            ramp_dt = float(self.ramp_dt.text().strip() or "1e-3")
            for sweeper in sweepers:
                utilities.ramp_voltage(
                    sweeper["channel"], 0, rampdV=ramp_dv, rampdT=ramp_dt
                )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Ramp Failed", str(exc))

    def _on_run(self) -> None:
        if self.station is None or not self.keithleys:
            QtWidgets.QMessageBox.warning(self, "Not Connected", "Connect to keithleys first.")
            return

        try:
            configs = self._collect_channel_configs()
            dt_list = self._parse_float_list(self.dt_list.text())
            delay_ratio = float(self.delayNPLC_ratio.text().strip() or "0.8")
            repeat = int(self.repeat.text().strip() or "1")
            round_delay = float(self.round_delay.text().strip() or "0")
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid Input", str(exc))
            return

        db_path = self.db_path.text().strip()
        if not db_path:
            QtWidgets.QMessageBox.warning(self, "Missing DB Path", "DB path is required.")
            return

        exp_name = self.exp_name.text().strip() or "gui_experiment"
        device_name = self.device_name.text().strip() or "device"
        csv_path = self.csv_path.text().strip()

        self.run_thread = QtCore.QThread()
        self.run_worker = RunWorker(
            station=self.station,
            keithleys=self.keithleys,
            configs=configs,
            dt_list=dt_list,
            delay_ratio=delay_ratio,
            repeat=repeat,
            round_delay=round_delay,
            db_path=db_path,
            exp_name=exp_name,
            device_name=device_name,
            csv_path=csv_path,
            ramp_up=self.ramp_up.isChecked(),
            ramp_down=self.ramp_down.isChecked(),
            time_independent=True,
        )
        self.run_worker.moveToThread(self.run_thread)
        self.run_thread.started.connect(self.run_worker.run)
        self.run_worker.finished.connect(self.run_thread.quit)
        self.run_worker.finished.connect(self.run_worker.deleteLater)
        self.run_thread.finished.connect(self.run_thread.deleteLater)
        self.run_worker.status.connect(self._on_worker_status)
        self.run_worker.error.connect(self._on_worker_error)
        self.run_worker.finished.connect(self._on_worker_finished)

        self._set_run_state(True, paused=False)
        self.run_thread.start()

    def _on_worker_status(self, msg: str) -> None:
        self.run_status.setText(msg)

    def _on_worker_error(self, msg: str) -> None:
        QtWidgets.QMessageBox.critical(self, "Run Failed", msg)

    def _on_worker_finished(self) -> None:
        self._set_run_state(False)

    @staticmethod
    def _load_yaml_instruments(path: str) -> list[str]:
        if yaml is None:
            raise RuntimeError("PyYAML not installed; install pyyaml to read configs")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        instruments = data.get("instruments", {})
        return list(instruments.keys())


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    icon_path = os.path.join(os.path.dirname(__file__), "icons", "control_icon.ico")
    if os.path.isfile(icon_path):
        app.setWindowIcon(QtGui.QIcon(icon_path))
    win = ArbitrarySweeperGUI()
    win.resize(1100, 800)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
