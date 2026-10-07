# Sets up this Windows machine as a sketchpad helper (screenshots only): two hidden logon
# tasks, the helper itself (deploy\windows\helper.ps1) and the reverse tunnel to the hub.
# Needs: Python 3.12+ from python.org or winget, key login to the hub with
# Windows' own ssh, and the shared secret in ~\.config\sketchpad\helper-token.
# Run in a normal (not admin) PowerShell:  powershell -ExecutionPolicy Bypass -File install.ps1
# -Wsl http://127.0.0.1:8793 also reaches a WSL helper, so its sessions show under this machine.
param([string]$Hub = "archbox", [int]$HubPort = 8792, [string]$Wsl = "")
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path

$token = Join-Path $HOME ".config\sketchpad\helper-token"
if (-not (Test-Path $token)) { throw "Missing $token (copy it from the hub)." }
if (-not (Get-Command python.exe -ErrorAction SilentlyContinue)) {
    throw "python.exe not found: winget install Python.Python.3.13"
}
& ssh -o BatchMode=yes -o ConnectTimeout=10 $Hub true
if ($LASTEXITCODE -ne 0) { throw "ssh $Hub needs key login first." }

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
# Defaults stop tasks on battery and after 3 days; a helper should just keep running.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

# Task restarts only cover a failed start, so helper.ps1 restarts the helper itself.
$helper = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $repo `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$repo\deploy\windows\helper.ps1`"$(if ($Wsl) { " -Wsl $Wsl" })"
Register-ScheduledTask -TaskName "sketchpad-helper" -Action $helper -Trigger $trigger `
    -Settings $settings -Force | Out-Null

$tunnel = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$repo\deploy\windows\tunnel.ps1`" -Hub $Hub -HubPort $HubPort"
Register-ScheduledTask -TaskName "sketchpad-tunnel" -Action $tunnel -Trigger $trigger `
    -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName "sketchpad-helper"
Start-ScheduledTask -TaskName "sketchpad-tunnel"
Write-Host "Started. On the hub add:  --remote <name>=http://127.0.0.1:$HubPort"
