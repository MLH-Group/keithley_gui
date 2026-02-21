# Keithley Lab Tools - Windows Install Guide

This guide is for lab users who want a one-time install and then launch the tools from desktop shortcuts.

## Prerequisites

1. Install Miniforge or Miniconda
   - Recommended: Miniforge (conda-forge)
   - Alternative: Miniconda or Anaconda
2. Install NI-VISA (and NI MAX recommended)
   - Required for VISA instrument communication
   - NI-VISA and NI MAX are installed separately from conda
3. Optional: Git
   - Only needed if you plan to `git clone` the repo instead of downloading a ZIP

## Get the Folder

Choose one option:

1. Git clone (recommended for updates)
   - `git clone <repo-url>`
2. Download ZIP
   - Click “Download ZIP” on the repo page and unzip it

## Install (One Time)

1. Open the repo folder
2. Double-click `scripts\install_windows.bat`
3. Wait for completion and confirm the desktop shortcuts appear

## Launch

Use the desktop shortcuts:

- Keithley GUI
- Keithley Plotter

If you re-run `scripts\install_windows.bat`, it will update the conda environment and refresh shortcuts.
