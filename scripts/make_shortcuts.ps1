param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$desktop = [Environment]::GetFolderPath("Desktop")
$wsh = New-Object -ComObject WScript.Shell

$guiLink = Join-Path $desktop "Keithley GUI.lnk"
$guiTarget = Join-Path $repoRoot "scripts\run_gui.bat"
$guiShortcut = $wsh.CreateShortcut($guiLink)
$guiShortcut.TargetPath = $guiTarget
$guiShortcut.WorkingDirectory = $repoRoot
$guiShortcut.Save()

$plotLink = Join-Path $desktop "Keithley Plotter.lnk"
$plotTarget = Join-Path $repoRoot "scripts\run_plotter.bat"
$plotShortcut = $wsh.CreateShortcut($plotLink)
$plotShortcut.TargetPath = $plotTarget
$plotShortcut.WorkingDirectory = $repoRoot
$plotShortcut.Save()
