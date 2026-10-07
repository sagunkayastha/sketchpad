# Runs the sketchpad helper and starts it again if it ever exits. Run hidden at logon by
# install.ps1; output goes to ~\.config\sketchpad\server.log.
# -Wsl http://127.0.0.1:8793 merges in a WSL helper's sessions (see README, "Windows + WSL");
# without it, the URL on one line in ~\.config\sketchpad\wsl does the same (read on each restart).
param([string]$Wsl = "")
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$log = Join-Path $HOME ".config\sketchpad\server.log"
$wslFile = Join-Path $HOME ".config\sketchpad\wsl"
Set-Location $repo
# Stopping the logon task ends this script but not its python.exe, so a restart would leave the
# old helper running next to the new one: stop any helper left over first.
function Stop-OldHelper {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*server.py helper*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}
while ($true) {
    Stop-OldHelper
    $url = if ($Wsl) { $Wsl } elseif (Test-Path $wslFile) { (Get-Content $wslFile -TotalCount 1).Trim() }
    $extra = if ($url) { @("--wsl", $url) } else { @() }
    & python.exe -u server.py helper --bind 127.0.0.1 @extra 2>&1 | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds 5
}
