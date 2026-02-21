from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Any

import numpy as np
from matplotlib import colors
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg, NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt5 import QtCore, QtGui, QtWidgets


@dataclass(frozen=True)
class ParamInfo:
    name: str
    label: str
    unit: str
    depends_on: tuple[str, ...]

    @property
    def display_label(self) -> str:
        if self.unit:
            return f"{self.label} ({self.unit})"
        return self.label


@dataclass(frozen=True)
class RunInfo:
    run_id: int
    exp_id: int
    name: str
    table: str
    is_completed: bool
    run_timestamp: float | None
    completed_timestamp: float | None
    parameters: tuple[str, ...]
    param_info: dict[str, ParamInfo]
    independent: tuple[str, ...]
    dependent: tuple[str, ...]


class DatabaseReader:
    def __init__(self) -> None:
        self.path: str | None = None
        self.conn: sqlite3.Connection | None = None

    def open(self, path: str) -> None:
        self.close()
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.path = path

    def close(self) -> None:
        if self.conn is not None:
            self.conn.close()
        self.conn = None
        self.path = None

    def list_runs(self) -> list[RunInfo]:
        if self.conn is None:
            return []
        rows = self.conn.execute(
            "SELECT run_id, exp_id, name, result_table_name, is_completed, "
            "run_timestamp, completed_timestamp, run_description, parameters "
            "FROM runs ORDER BY run_id DESC"
        ).fetchall()
        runs: list[RunInfo] = []
        for row in rows:
            run_description = self._parse_json(row["run_description"])
            param_info, param_order = self._parse_param_info(
                run_description, row["parameters"]
            )
            dependencies = {
                name: list(info.depends_on) for name, info in param_info.items()
            }
            independent = [p for p in param_order if not dependencies.get(p)]
            dependent = [p for p in param_order if dependencies.get(p)]
            if not dependent and len(param_order) > 1:
                independent = [param_order[0]]
                dependent = param_order[1:]
            runs.append(
                RunInfo(
                    run_id=int(row["run_id"]),
                    exp_id=int(row["exp_id"]),
                    name=str(row["name"] or ""),
                    table=str(row["result_table_name"]),
                    is_completed=bool(row["is_completed"]),
                    run_timestamp=float(row["run_timestamp"]) if row["run_timestamp"] else None,
                    completed_timestamp=float(row["completed_timestamp"]) if row["completed_timestamp"] else None,
                    parameters=tuple(param_order),
                    param_info=param_info,
                    independent=tuple(independent),
                    dependent=tuple(dependent),
                )
            )
        return runs

    def read_table_columns(self, table: str) -> list[str]:
        if self.conn is None:
            return []
        rows = self.conn.execute(f"PRAGMA table_info('{table}')").fetchall()
        return [row["name"] for row in rows]

    @staticmethod
    def _parse_json(raw: Any) -> dict[str, Any]:
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _parse_param_info(
        self, run_description: dict[str, Any], parameters_raw: Any
    ) -> tuple[dict[str, ParamInfo], list[str]]:
        param_info: dict[str, ParamInfo] = {}
        order: list[str] = []

        def add_order(name: str) -> None:
            if name and name not in order:
                order.append(name)

        paramspecs = (
            run_description.get("interdependencies", {}) or {}
        ).get("paramspecs", [])
        for spec in paramspecs:
            name = str(spec.get("name", ""))
            if not name:
                continue
            add_order(name)
            param_info[name] = ParamInfo(
                name=name,
                label=str(spec.get("label") or name),
                unit=str(spec.get("unit") or ""),
                depends_on=tuple(spec.get("depends_on") or []),
            )

        interdeps = (run_description.get("interdependencies_", {}) or {})
        if interdeps:
            deps = interdeps.get("dependencies", {}) or {}
            params = interdeps.get("parameters", {}) or {}
            for name, meta in params.items():
                add_order(name)
                info = param_info.get(name)
                if info is None:
                    param_info[name] = ParamInfo(
                        name=name,
                        label=str(meta.get("label") or name),
                        unit=str(meta.get("unit") or ""),
                        depends_on=tuple(deps.get(name) or []),
                    )
                elif deps.get(name):
                    param_info[name] = ParamInfo(
                        name=info.name,
                        label=info.label,
                        unit=info.unit,
                        depends_on=tuple(deps.get(name) or []),
                    )

        if parameters_raw:
            for name in str(parameters_raw).split(","):
                add_order(name.strip())

        for name in list(param_info.keys()):
            if name not in order:
                add_order(name)

        for name in order:
            if name in param_info:
                continue
            param_info[name] = ParamInfo(
                name=name,
                label=name,
                unit="",
                depends_on=tuple(),
            )

        return param_info, order


class LivePlotCanvas(FigureCanvasQTAgg):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        self.fig = Figure(figsize=(7, 5))
        super().__init__(self.fig)
        self.setParent(parent)


class LivePlotterGUI(QtWidgets.QMainWindow):
    def __init__(self, initial_db_path: str | None = None) -> None:
        super().__init__()
        self.setWindowTitle("QCoDeS Live Plotter")
        self.setAcceptDrops(True)

        self.reader = DatabaseReader()
        self.runs: list[RunInfo] = []
        self.current_run: RunInfo | None = None
        self.table_columns: list[str] = []
        self.data: dict[str, list[Any]] = {}
        self.last_id: int = 0
        self.plot_state: tuple[Any, ...] | None = None
        self.colorbar = None
        self.scatter = None
        self.line_handles: dict[str, Any] = {}
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
        self.cmap = "viridis"

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        main = QtWidgets.QHBoxLayout(root)

        left_panel = self._build_left_panel()
        right_panel = self._build_right_panel()

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.addWidget(left_panel)
        split.addWidget(right_panel)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 820])
        split.setHandleWidth(6)
        split.setStyleSheet("QSplitter::handle{background: #c0c0c0;}")
        main.addWidget(split, 1)

        self._build_menu()
        self._configure_timer()
        if initial_db_path:
            self._load_db(initial_db_path, preserve_state=False)

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        load_action = QtWidgets.QAction("Load DB", self)
        load_action.triggered.connect(self._on_load_db)
        file_menu.addAction(load_action)

    def _build_left_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        self.db_label = QtWidgets.QLabel("DB: (none)")
        self.db_label.setWordWrap(True)
        layout.addWidget(self.db_label)

        export_row = QtWidgets.QHBoxLayout()
        self.csv_path = QtWidgets.QLineEdit("")
        self.export_btn = QtWidgets.QPushButton("Export CSV")
        self.export_btn.clicked.connect(self._on_export_csv)
        export_row.addWidget(self.csv_path, 1)
        export_row.addWidget(self.export_btn)
        layout.addLayout(export_row)

        self.load_btn = QtWidgets.QPushButton("Load DB")
        self.load_btn.clicked.connect(self._on_load_db)
        layout.addWidget(self.load_btn)
        self.refresh_db_btn = QtWidgets.QPushButton("Refresh DB")
        self.refresh_db_btn.clicked.connect(self._on_refresh_db)
        layout.addWidget(self.refresh_db_btn)

        layout.addWidget(QtWidgets.QLabel("Runs / Result Tables"))
        self.run_tree = QtWidgets.QTreeWidget()
        self.run_tree.setHeaderHidden(True)
        self.run_tree.itemDoubleClicked.connect(self._on_run_tree_selected)
        layout.addWidget(self.run_tree, 1)

        return panel

    def _build_right_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        controls = QtWidgets.QWidget()
        controls_layout = QtWidgets.QVBoxLayout(controls)

        self.run_summary = QtWidgets.QLabel("No run selected.")
        self.run_summary.setWordWrap(True)
        controls_layout.addWidget(self.run_summary)

        vars_panel = self._build_variables_panel()
        controls_layout.addWidget(vars_panel)

        options_panel = self._build_options_panel()
        controls_layout.addWidget(options_panel)

        plot_panel = self._build_plot_panel()

        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(controls)
        split.addWidget(plot_panel)
        split.setChildrenCollapsible(False)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([240, 560])
        split.setHandleWidth(6)
        split.setStyleSheet("QSplitter::handle{background: #c0c0c0;}")

        layout.addWidget(split, 1)
        return panel

    def _build_variables_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QGroupBox("Variables")
        layout = QtWidgets.QHBoxLayout(panel)

        axis_group = QtWidgets.QGroupBox("Independent Axes")
        axis_layout = QtWidgets.QVBoxLayout(axis_group)
        combo_layout = QtWidgets.QFormLayout()
        self.x_combo = QtWidgets.QComboBox()
        self.y_combo = QtWidgets.QComboBox()
        self.x_combo.currentIndexChanged.connect(self._on_plot_settings_changed)
        self.y_combo.currentIndexChanged.connect(self._on_plot_settings_changed)
        combo_layout.addRow("X Axis", self.x_combo)
        combo_layout.addRow("Y Axis", self.y_combo)
        axis_layout.addLayout(combo_layout)

        dep_group = QtWidgets.QGroupBox("Dependent")
        dep_layout = QtWidgets.QVBoxLayout(dep_group)
        self.dep_list = QtWidgets.QListWidget()
        self.dep_list.itemChanged.connect(self._on_plot_settings_changed)
        dep_layout.addWidget(self.dep_list)

        layout.addWidget(axis_group, 0)
        layout.addWidget(dep_group, 1)
        return panel

    def _build_options_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QGroupBox("Plot Options")
        layout = QtWidgets.QGridLayout(panel)

        self.overlay_radio = QtWidgets.QRadioButton("Overlay")
        self.subplot_radio = QtWidgets.QRadioButton("Subplots")
        self.overlay_radio.setChecked(True)
        self.overlay_radio.toggled.connect(self._on_plot_settings_changed)

        self.auto_refresh = QtWidgets.QCheckBox("Auto Refresh")
        self.auto_refresh.setChecked(True)
        self.auto_refresh.toggled.connect(self._on_refresh_toggle)
        self.refresh_interval = QtWidgets.QDoubleSpinBox()
        self.refresh_interval.setSuffix(" s")
        self.refresh_interval.setDecimals(2)
        self.refresh_interval.setRange(0.1, 60.0)
        self.refresh_interval.setValue(1.0)
        self.refresh_interval.valueChanged.connect(self._on_refresh_interval_changed)
        self.refresh_now_btn = QtWidgets.QPushButton("Refresh Now")
        self.refresh_now_btn.clicked.connect(self._refresh_now)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)

        layout.addWidget(self.overlay_radio, 0, 0)
        layout.addWidget(self.subplot_radio, 0, 1)
        layout.addWidget(self.auto_refresh, 0, 2)
        layout.addWidget(self.refresh_interval, 0, 3)
        layout.addWidget(self.refresh_now_btn, 0, 4)
        layout.addWidget(self.status_label, 1, 0, 1, 5)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 0)
        layout.setColumnStretch(3, 0)
        layout.setColumnStretch(4, 1)
        return panel

    def _build_plot_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QGroupBox("Live Plot")
        layout = QtWidgets.QVBoxLayout(panel)
        self.plot = LivePlotCanvas()
        self.toolbar = NavigationToolbar2QT(self.plot, self)
        self.toolbar.setIconSize(QtCore.QSize(16, 16))
        self.log_x = QtWidgets.QCheckBox("Log X")
        self.log_y = QtWidgets.QCheckBox("Log Y")
        self.log_z = QtWidgets.QCheckBox("Log Z")
        self.abs_x = QtWidgets.QCheckBox("Abs X")
        self.abs_y = QtWidgets.QCheckBox("Abs Y")
        self.abs_z = QtWidgets.QCheckBox("Abs Z")
        for widget in (self.log_x, self.log_y, self.log_z):
            widget.toggled.connect(self._on_plot_settings_changed)
        for widget in (self.abs_x, self.abs_y, self.abs_z):
            widget.toggled.connect(self._on_plot_settings_changed)

        toolbar_row = QtWidgets.QHBoxLayout()
        toolbar_row.addWidget(self.toolbar)
        toolbar_row.addWidget(self.log_x)
        toolbar_row.addWidget(self.log_y)
        toolbar_row.addWidget(self.log_z)
        toolbar_row.addWidget(self.abs_x)
        toolbar_row.addWidget(self.abs_y)
        toolbar_row.addWidget(self.abs_z)
        toolbar_row.addStretch(1)
        layout.addLayout(toolbar_row)
        layout.addWidget(self.plot, 1)
        return panel

    def _configure_timer(self) -> None:
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._refresh_now)
        self._on_refresh_interval_changed()
        if self.auto_refresh.isChecked():
            self.timer.start()

    def dragEnterEvent(self, event: QtCore.QEvent) -> None:
        if isinstance(event, QtGui.QDragEnterEvent):
            if event.mimeData().hasUrls():
                for url in event.mimeData().urls():
                    if url.toLocalFile().lower().endswith(".db"):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event: QtCore.QEvent) -> None:
        if isinstance(event, QtGui.QDropEvent):
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path.lower().endswith(".db"):
                    self._load_db(path)
                    event.acceptProposedAction()
                    return
        event.ignore()

    def _on_load_db(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load QCoDeS DB", "", "SQLite DB (*.db)"
        )
        if path:
            self._load_db(path)

    def _on_refresh_db(self) -> None:
        db_path = self.reader.path
        if not db_path:
            self.status_label.setText("No DB loaded to refresh.")
            return
        selected_run_id = self.current_run.run_id if self.current_run is not None else None
        try:
            self.reader.open(db_path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Failed To Refresh DB", str(exc))
            return
        self._load_runs(select_run_id=selected_run_id, preserve_plot=True)
        if self.current_run is not None:
            self._refresh_now()
        else:
            self.status_label.setText("DB refreshed.")

    def _load_db(self, path: str, preserve_state: bool = False) -> None:
        if not os.path.isfile(path):
            QtWidgets.QMessageBox.warning(self, "Missing File", f"Could not find:\n{path}")
            return
        selected_run_id = self.current_run.run_id if preserve_state and self.current_run else None
        x_name = self.x_combo.currentText().strip() if preserve_state else ""
        y_name = self.y_combo.currentText().strip() if preserve_state else "(none)"
        dep_checks = set(self._checked_dependents()) if preserve_state else set()
        try:
            self.reader.open(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Failed To Open DB", str(exc))
            return
        self.db_label.setText(f"DB: {path}")
        self._update_csv_path_default(path)
        self._load_runs(select_run_id=selected_run_id)
        if preserve_state and self.current_run is not None:
            self._restore_plot_selection(x_name, y_name, dep_checks)
            self._refresh_now()

    def _update_csv_path_default(self, db_path: str) -> None:
        if not db_path:
            return
        base, _ = os.path.splitext(db_path)
        self.csv_path.setText(base + ".csv")

    def _load_runs(
        self,
        select_run_id: int | None = None,
        preserve_plot: bool = False,
    ) -> None:
        previous_run = self.current_run
        self.run_tree.clear()
        self.runs = []
        if not preserve_plot:
            self.current_run = None
        try:
            self.runs = self.reader.list_runs()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Failed To Read Runs", str(exc))
            return
        groups: dict[str, QtWidgets.QTreeWidgetItem] = {}
        first_group: QtWidgets.QTreeWidgetItem | None = None
        selected_item: QtWidgets.QTreeWidgetItem | None = None
        selected_run: RunInfo | None = None
        for run in self.runs:
            status = "completed" if run.is_completed else "live"
            label = f"Run {run.run_id} | {run.name} | {run.table} | {status}"
            date_key = self._date_key(run)
            group = groups.get(date_key)
            if group is None:
                group = QtWidgets.QTreeWidgetItem([date_key])
                group.setFirstColumnSpanned(True)
                self.run_tree.addTopLevelItem(group)
                groups[date_key] = group
                if first_group is None:
                    first_group = group
            child = QtWidgets.QTreeWidgetItem([label])
            child.setData(0, QtCore.Qt.UserRole, run.run_id)
            group.addChild(child)
            if select_run_id is not None and run.run_id == select_run_id:
                selected_item = child
                selected_run = run
        if selected_item is not None and selected_run is not None:
            parent = selected_item.parent()
            if parent is not None:
                parent.setExpanded(True)
            self.run_tree.setCurrentItem(selected_item)
            if (
                preserve_plot
                and previous_run is not None
                and previous_run.run_id == selected_run.run_id
                and previous_run.table == selected_run.table
            ):
                self.current_run = selected_run
                self.run_summary.setText(
                    f"Run {selected_run.run_id} | Exp {selected_run.exp_id} | {selected_run.name} | {selected_run.table}"
                )
                return
            self._select_run(selected_run)
            return
        if first_group is not None:
            first_group.setExpanded(True)
            if first_group.childCount() > 0:
                self.run_tree.setCurrentItem(first_group.child(0))
                self._select_run(self.runs[0])
        if self.runs:
            return
        self.current_run = None
        self.run_summary.setText("No run selected.")

    def _on_run_tree_selected(self, item: QtWidgets.QTreeWidgetItem) -> None:
        run_id = item.data(0, QtCore.Qt.UserRole)
        if run_id is None:
            item.setExpanded(not item.isExpanded())
            return
        run = next((r for r in self.runs if r.run_id == run_id), None)
        if run is None:
            return
        self._select_run(run)

    def _select_run(self, run: RunInfo) -> None:
        self.current_run = run
        self.run_summary.setText(
            f"Run {run.run_id} | Exp {run.exp_id} | {run.name} | {run.table}"
        )
        self._reset_data()
        self._populate_variable_lists()
        self._refresh_now()

    def _populate_variable_lists(self) -> None:
        self.dep_list.blockSignals(True)
        self.dep_list.clear()
        self.x_combo.blockSignals(True)
        self.y_combo.blockSignals(True)
        self.x_combo.clear()
        self.y_combo.clear()
        if self.current_run is None:
            self.dep_list.blockSignals(False)
            self.x_combo.blockSignals(False)
            self.y_combo.blockSignals(False)
            return
        param_names = list(self.current_run.parameters)
        table_names = [c for c in self.table_columns if c != "id"]
        if table_names:
            param_names = table_names
        for name in param_names:
            info = self.current_run.param_info.get(name)
            label = info.display_label if info else name
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, name)
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(QtCore.Qt.Unchecked)
            self.dep_list.addItem(item)
        params = list(param_names)
        if params:
            self.x_combo.addItems(params)
            self.y_combo.addItem("(none)")
            self.y_combo.addItems(params)
        self.dep_list.blockSignals(False)
        self.x_combo.blockSignals(False)
        self.y_combo.blockSignals(False)
        self.plot_state = None

    def _reset_data(self) -> None:
        self.data = {}
        self.table_columns = []
        self.last_id = 0
        if self.current_run is None:
            return
        self.table_columns = self.reader.read_table_columns(self.current_run.table)
        for col in self.table_columns:
            if col == "id":
                continue
            self.data[col] = []

    def _refresh_now(self) -> None:
        if self.current_run is None or self.reader.conn is None:
            return
        new_rows = self._fetch_new_rows()
        if new_rows:
            self._update_plot()
        self.status_label.setText(
            f"Rows: {self.last_id} | +{new_rows} new"
        )

    def _fetch_new_rows(self) -> int:
        if self.reader.conn is None or self.current_run is None:
            return 0
        table = self.current_run.table
        try:
            max_row = self.reader.conn.execute(
                f"SELECT max(id) as max_id FROM '{table}'"
            ).fetchone()
        except Exception:
            return 0
        max_id = max_row["max_id"] if max_row else None
        if max_id is None or max_id <= self.last_id:
            return 0
        rows = self.reader.conn.execute(
            f"SELECT * FROM '{table}' WHERE id > ? ORDER BY id",
            (self.last_id,),
        ).fetchall()
        if not rows:
            return 0
        for row in rows:
            for col, values in self.data.items():
                values.append(row[col])
        self.last_id = int(rows[-1]["id"])
        return len(rows)

    def _on_plot_settings_changed(self) -> None:
        self._update_plot(force_rebuild=True)

    def _update_plot(self, force_rebuild: bool = False) -> None:
        if self.current_run is None:
            return
        deps = self._checked_dependents()

        x_name = self.x_combo.currentText().strip()
        y2_name = self.y_combo.currentText().strip()
        is_2d = y2_name and y2_name != "(none)"
        if is_2d and not deps:
            deps = self._auto_select_dependent(x_name, y2_name)
            if not deps:
                self.status_label.setText("Select a dependent variable for 2D map.")
                return
        if not deps:
            return
        mode = "subplot" if self.subplot_radio.isChecked() else "overlay"
        state = (is_2d, mode, tuple(deps), x_name, y2_name)
        if force_rebuild or state != self.plot_state:
            self._rebuild_plot(deps, x_name, y2_name, is_2d, mode)
            self.plot_state = state
        else:
            self._update_existing_plot(deps, x_name, y2_name, is_2d, mode)

    def _rebuild_plot(
        self,
        deps: list[str],
        x_name: str,
        y2_name: str,
        is_2d: bool,
        mode: str,
    ) -> None:
        self.plot.fig.clear()
        self.colorbar = None
        self.scatter = None
        self.line_handles = {}
        if is_2d:
            dep = deps[0]
            ax = self.plot.fig.add_subplot(1, 1, 1)
            self.scatter = ax.scatter([], [], c=[], s=18, cmap=self.cmap)
            self._format_axes(ax, x_name, y2_name, dep, is_2d=True)
            self.colorbar = self.plot.fig.colorbar(self.scatter, ax=ax)
        else:
            if mode == "subplot":
                nrows, ncols = self._subplot_grid(len(deps))
                for idx, dep in enumerate(deps, start=1):
                    ax = self.plot.fig.add_subplot(nrows, ncols, idx)
                    ax.set_prop_cycle(color=self.color_cycle)
                    line, = ax.plot([], [], linestyle="-", marker="o", markersize=3, linewidth=1)
                    self.line_handles[dep] = line
                    self._format_axes(ax, x_name, "", dep, is_2d=False, overlay=False)
            else:
                ax = self.plot.fig.add_subplot(1, 1, 1)
                ax.set_prop_cycle(color=self.color_cycle)
                for dep in deps:
                    line, = ax.plot([], [], linestyle="-", marker="o", markersize=3, linewidth=1, label=self._label_for(dep))
                    self.line_handles[dep] = line
                ax.legend(loc="best")
                self._format_axes(ax, x_name, "", deps[0], is_2d=False, overlay=True)
        self.plot.fig.tight_layout()
        self._update_existing_plot(deps, x_name, y2_name, is_2d, mode)
        self.plot.draw()

    def _update_existing_plot(
        self,
        deps: list[str],
        x_name: str,
        y2_name: str,
        is_2d: bool,
        mode: str,
    ) -> None:
        if is_2d:
            self._update_2d_plot(deps[0], x_name, y2_name)
        else:
            self._update_1d_plot(deps, x_name, mode == "overlay")
        self.plot.draw_idle()

    def _update_1d_plot(self, deps: list[str], x_name: str, overlay: bool) -> None:
        x = self._values_for(x_name)
        if x.size == 0:
            return
        if overlay:
            for dep in deps:
                line = self.line_handles.get(dep)
                if line is None:
                    continue
                y = self._values_for(dep)
                x_plot, y_plot, mask = self._prepare_xy(
                    x,
                    y,
                    self.log_x.isChecked(),
                    self.log_y.isChecked(),
                    self.abs_x.isChecked(),
                    self.abs_y.isChecked(),
                )
                line.set_data(x_plot[mask], y_plot[mask])
            ax = self.plot.fig.axes[0] if self.plot.fig.axes else None
            if ax is not None:
                ax.legend(loc="best")
                self._format_axes(ax, x_name, "", deps[0], is_2d=False)
        else:
            for dep in deps:
                line = self.line_handles.get(dep)
                if line is None:
                    continue
                ax = line.axes
                y = self._values_for(dep)
                x_plot, y_plot, mask = self._prepare_xy(
                    x,
                    y,
                    self.log_x.isChecked(),
                    self.log_y.isChecked(),
                    self.abs_x.isChecked(),
                    self.abs_y.isChecked(),
                )
                line.set_data(x_plot[mask], y_plot[mask])
                self._format_axes(ax, x_name, "", dep, is_2d=False, overlay=False)

    def _update_2d_plot(self, dep: str, x_name: str, y_name: str) -> None:
        ax = self.plot.fig.axes[0] if self.plot.fig.axes else None
        if ax is None:
            return
        x = self._values_for(x_name)
        y = self._values_for(y_name)
        z = self._values_for(dep)
        x_plot, y_plot, z_plot, mask = self._prepare_xyz(
            x,
            y,
            z,
            self.log_x.isChecked(),
            self.log_y.isChecked(),
            self.log_z.isChecked(),
            self.abs_x.isChecked(),
            self.abs_y.isChecked(),
            self.abs_z.isChecked(),
        )
        x_full = x_plot
        y_full = y_plot
        x = x_plot[mask]
        y = y_plot[mask]
        z = z_plot[mask]
        if x.size == 0 or y.size == 0 or z.size == 0:
            self.status_label.setText(
                f"No data for 2D map: x={x_name}, y={y_name}, z={dep}"
            )
            return
        if self.scatter is None:
            self.scatter = ax.scatter(x, y, c=z, s=12)
        else:
            self.scatter.set_offsets(np.column_stack([x, y]))
            self.scatter.set_array(z)
        zmin, zmax = float(np.nanmin(z)), float(np.nanmax(z))
        if not np.isfinite(zmin) or not np.isfinite(zmax):
            zmin, zmax = 0.0, 1.0
        elif zmin == zmax:
            zmax = zmin + (abs(zmin) * 0.01 + 1e-12)
        if self.log_z.isChecked():
            self.scatter.set_norm(colors.LogNorm())
        else:
            self.scatter.set_norm(colors.Normalize())
        self.scatter.set_clim(zmin, zmax)
        if self.colorbar is not None:
            self.colorbar.update_normal(self.scatter)
        self._apply_axis_limits(
            ax,
            x_full,
            y_full,
            self.log_x.isChecked(),
            self.log_y.isChecked(),
        )
        self._format_axes(ax, x_name, y_name, dep, is_2d=True)

    def _format_axes(
        self,
        ax: Any,
        x_name: str,
        y_name: str,
        dep_name: str,
        is_2d: bool,
        overlay: bool = True,
    ) -> None:
        ax.set_xscale("log" if self.log_x.isChecked() else "linear")
        if is_2d:
            ax.set_yscale("log" if self.log_y.isChecked() else "linear")
        else:
            ax.set_yscale("log" if self.log_y.isChecked() else "linear")
        x_label = self._label_for(x_name) if x_name else "X"
        if is_2d:
            y_label = self._label_for(y_name) if y_name else "Y"
            ax.set_xlabel(x_label)
            ax.set_ylabel(y_label)
            ax.set_title(self._label_for(dep_name))
        else:
            ax.set_xlabel(x_label)
            if overlay:
                ax.set_ylabel("Dependent")
                ax.set_title("Overlay")
            else:
                ax.set_ylabel(self._label_for(dep_name))
        if is_2d:
            ax.autoscale()
        else:
            ax.relim()
            ax.autoscale_view()
        ax.grid(True, which="both", alpha=0.3, linestyle="--", linewidth=0.6)

    def _label_for(self, name: str) -> str:
        if self.current_run is None:
            return name
        info = self.current_run.param_info.get(name)
        return info.display_label if info else name

    @staticmethod
    def _subplot_grid(count: int) -> tuple[int, int]:
        if count <= 1:
            return 1, 1
        ncols = int(math.ceil(math.sqrt(count)))
        nrows = int(math.ceil(count / ncols))
        return nrows, ncols

    def _values_for(self, name: str) -> np.ndarray:
        values = self.data.get(name, [])
        if not values:
            return np.array([], dtype=float)
        return np.array(
            [v if v is not None else np.nan for v in values], dtype=float
        )

    @staticmethod
    def _forward_fill(values: np.ndarray) -> np.ndarray:
        if values.size == 0:
            return values
        mask = np.isfinite(values)
        if not mask.any():
            return values
        idx = np.where(mask, np.arange(values.size), -1)
        idx = np.maximum.accumulate(idx)
        filled = values.copy()
        valid = idx >= 0
        filled[valid] = values[idx[valid]]
        return filled

    @staticmethod
    def _nearest_fill(values: np.ndarray) -> np.ndarray:
        if values.size == 0:
            return values
        mask = np.isfinite(values)
        if not mask.any():
            return values
        fwd = LivePlotterGUI._forward_fill(values)
        bwd = LivePlotterGUI._forward_fill(values[::-1])[::-1]
        filled = fwd.copy()
        nan_mask = ~np.isfinite(fwd)
        filled[nan_mask] = bwd[nan_mask]
        return filled

    def _prepare_xy(
        self,
        x: np.ndarray,
        y: np.ndarray,
        log_x: bool,
        log_y: bool,
        abs_x: bool,
        abs_y: bool,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        if abs_x:
            x = np.abs(x)
        if abs_y:
            y = np.abs(y)
        mask = self._mask_valid(x, y, log_x=log_x, log_y=log_y)
        if mask.any():
            return x, y, mask
        x_fill = self._nearest_fill(x)
        y_fill = self._nearest_fill(y)
        mask = self._mask_valid(x_fill, y_fill, log_x=log_x, log_y=log_y)
        return x_fill, y_fill, mask

    def _prepare_xyz(
        self,
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray,
        log_x: bool,
        log_y: bool,
        log_z: bool,
        abs_x: bool,
        abs_y: bool,
        abs_z: bool,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        if abs_x:
            x = np.abs(x)
        if abs_y:
            y = np.abs(y)
        if abs_z:
            z = np.abs(z)
        mask = self._mask_valid(x, y, z, log_x=log_x, log_y=log_y, log_z=log_z)
        if mask.any():
            return x, y, z, mask
        x_fill = self._nearest_fill(x)
        y_fill = self._nearest_fill(y)
        mask = self._mask_valid(
            x_fill, y_fill, z, log_x=log_x, log_y=log_y, log_z=log_z
        )
        return x_fill, y_fill, z, mask

    @staticmethod
    def _axis_limits(values: np.ndarray, log: bool) -> tuple[float, float] | None:
        if values.size == 0:
            return None
        vals = values[np.isfinite(values)]
        if log:
            vals = vals[vals > 0]
        if vals.size == 0:
            return None
        vmin = float(np.nanmin(vals))
        vmax = float(np.nanmax(vals))
        if vmin == vmax:
            vmax = vmin + (abs(vmin) * 0.01 + 1e-12)
        return vmin, vmax

    def _apply_axis_limits(
        self,
        ax: Any,
        x_values: np.ndarray,
        y_values: np.ndarray,
        log_x: bool,
        log_y: bool,
    ) -> None:
        xlim = self._axis_limits(x_values, log_x)
        ylim = self._axis_limits(y_values, log_y)
        if xlim:
            ax.set_xlim(xlim)
        if ylim:
            ax.set_ylim(ylim)

    @staticmethod
    def _mask_valid(
        x: np.ndarray,
        y: np.ndarray,
        z: np.ndarray | None = None,
        log_x: bool = False,
        log_y: bool = False,
        log_z: bool = False,
    ) -> np.ndarray:
        mask = np.isfinite(x) & np.isfinite(y)
        if z is not None:
            mask &= np.isfinite(z)
        if log_x:
            mask &= x > 0
        if log_y:
            mask &= y > 0
        if z is not None and log_z:
            mask &= z > 0
        return mask

    def _checked_dependents(self) -> list[str]:
        deps = []
        for idx in range(self.dep_list.count()):
            item = self.dep_list.item(idx)
            if item.checkState() == QtCore.Qt.Checked:
                name = item.data(QtCore.Qt.UserRole)
                deps.append(name)
        return deps

    def _auto_select_dependent(self, x_name: str, y_name: str) -> list[str]:
        if self.current_run is None:
            return []
        preferred = [
            p for p in self.current_run.parameters if p not in (x_name, y_name)
        ]
        if not preferred:
            preferred = list(self.current_run.parameters)
        if not preferred:
            return []
        dep = preferred[0]
        for idx in range(self.dep_list.count()):
            item = self.dep_list.item(idx)
            if item.data(QtCore.Qt.UserRole) == dep:
                item.setCheckState(QtCore.Qt.Checked)
                break
        return [dep]

    def _restore_plot_selection(
        self,
        x_name: str,
        y_name: str,
        dep_checks: set[str],
    ) -> None:
        if self.current_run is None:
            return
        self.x_combo.blockSignals(True)
        self.y_combo.blockSignals(True)
        x_idx = self.x_combo.findText(x_name)
        if x_idx >= 0:
            self.x_combo.setCurrentIndex(x_idx)
        y_idx = self.y_combo.findText(y_name)
        if y_idx >= 0:
            self.y_combo.setCurrentIndex(y_idx)
        self.x_combo.blockSignals(False)
        self.y_combo.blockSignals(False)

        self.dep_list.blockSignals(True)
        for idx in range(self.dep_list.count()):
            item = self.dep_list.item(idx)
            name = item.data(QtCore.Qt.UserRole)
            item.setCheckState(
                QtCore.Qt.Checked if name in dep_checks else QtCore.Qt.Unchecked
            )
        self.dep_list.blockSignals(False)
        self._update_plot(force_rebuild=True)

    def _on_refresh_interval_changed(self) -> None:
        interval_ms = int(self.refresh_interval.value() * 1000)
        self.timer.setInterval(interval_ms)

    def _on_refresh_toggle(self) -> None:
        if self.auto_refresh.isChecked():
            self.timer.start()
        else:
            self.timer.stop()

    def _date_key(self, run: RunInfo) -> str:
        ts = run.completed_timestamp or run.run_timestamp
        if not ts:
            return "Unknown Date"
        if ts > 1e12:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return "Unknown Date"

    def _on_export_csv(self) -> None:
        if self.current_run is None or self.reader.path is None:
            QtWidgets.QMessageBox.warning(
                self, "No Run Selected", "Select a run before exporting."
            )
            return
        try:
            from qcodes.dataset import initialise_or_create_database_at, load_by_id
            import pandas as pd  # type: ignore
        except Exception as exc:
            QtWidgets.QMessageBox.critical(
                self, "Missing Dependencies", f"Export requires qcodes + pandas.\n{exc}"
            )
            return
        db_path = self.reader.path
        output_csv = self.csv_path.text().strip() or None
        try:
            initialise_or_create_database_at(db_path)
            ds = load_by_id(self.current_run.run_id)
            df = ds.to_pandas_dataframe()
            if df.index.name is not None or isinstance(df.index, pd.MultiIndex):
                df = df.reset_index()
            if output_csv is None:
                db_name = os.path.splitext(os.path.basename(db_path))[0]
                exp_name = ds.exp_name.replace(" ", "_")
                output_csv = os.path.join(
                    os.path.dirname(db_path), f"{db_name}_{exp_name}_run{self.current_run.run_id}.csv"
                )
            df = df.astype("float", errors="ignore")
            df.to_csv(output_csv, index=False)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Export Failed", str(exc))
            return
        self.csv_path.setText(output_csv)
        QtWidgets.QMessageBox.information(
            self, "Export Complete", f"Saved CSV to:\n{output_csv}"
        )


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    initial_db_path: str | None = None
    args = sys.argv[1:]
    if args:
        if args[0] == "--db" and len(args) > 1:
            initial_db_path = args[1]
        else:
            initial_db_path = args[0]
    win = LivePlotterGUI(initial_db_path=initial_db_path)
    win.resize(1200, 800)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
