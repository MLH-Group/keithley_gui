param()

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = (Resolve-Path (Join-Path $scriptDir "..")).Path
$desktop = [Environment]::GetFolderPath("Desktop")
$wsh = New-Object -ComObject WScript.Shell

$guiLink = Join-Path $desktop "Keithley Control.lnk"
$guiTarget = "$env:ComSpec"
$guiArgs = "/c `"`"$(Join-Path $repoRoot 'scripts\run_gui.bat')`"`""
$guiIcon = Join-Path $repoRoot "icons\control_icon.ico"
$guiShortcut = $wsh.CreateShortcut($guiLink)
$guiShortcut.TargetPath = $guiTarget
$guiShortcut.Arguments = $guiArgs
$guiShortcut.WorkingDirectory = $repoRoot
$guiShortcut.IconLocation = "$guiIcon,0"
$guiShortcut.Save()

$plotLink = Join-Path $desktop "Keithley Plotter.lnk"
$plotTarget = "$env:ComSpec"
$plotArgs = "/c `"`"$(Join-Path $repoRoot 'scripts\run_plotter.bat')`"`""
$plotIcon = Join-Path $repoRoot "icons\plotter_icon.ico"
$plotShortcut = $wsh.CreateShortcut($plotLink)
$plotShortcut.TargetPath = $plotTarget
$plotShortcut.Arguments = $plotArgs
$plotShortcut.WorkingDirectory = $repoRoot
$plotShortcut.IconLocation = "$plotIcon,0"
$plotShortcut.Save()
