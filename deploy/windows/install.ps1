# Sets up this Windows machine as a sketchpad helper (screenshots only): two logon tasks,
# the helper itself (pythonw, no window) and the reverse tunnel to the hub.
# Needs: Python 3.12+ from python.org or winget (for pythonw.exe), key login to the hub with
# Windows' own ssh, and the shared secret in ~\.config\sketchpad\helper-token.
# Run in a normal (not admin) PowerShell:  powershell -ExecutionPolicy Bypass -File install.ps1
param([string]$Hub = "archbox", [int]$HubPort = 8792)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path

$token = Join-Path $HOME ".config\sketchpad\helper-token"
if (-not (Test-Path $token)) { throw "Missing $token (copy it from the hub)." }
$pythonw = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $pythonw) { throw "pythonw.exe not found: winget install Python.Python.3.13" }
& ssh -o BatchMode=yes -o ConnectTimeout=10 $Hub true
if ($LASTEXITCODE -ne 0) { throw "ssh $Hub needs key login first." }

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Defaults stop tasks on battery and after 3 days; a helper should just keep running.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

$helper = New-ScheduledTaskAction -Execute $pythonw -WorkingDirectory $repo `
    -Argument "server.py helper --bind 127.0.0.1"
Register-ScheduledTask -TaskName "sketchpad-helper" -Action $helper -Trigger $trigger `
    -Settings $settings -Force | Out-Null

$tunnel = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$repo\deploy\windows\tunnel.ps1`" -Hub $Hub -HubPort $HubPort"
Register-ScheduledTask -TaskName "sketchpad-tunnel" -Action $tunnel -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName "sketchpad-helper"
Start-ScheduledTask -TaskName "sketchpad-tunnel"
Write-Host "Started. On the hub add:  --remote <name>=http://127.0.0.1:$HubPort"
