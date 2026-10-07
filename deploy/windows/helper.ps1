# Runs the sketchpad helper and starts it again if it ever exits. Run hidden at logon by
# install.ps1; output goes to ~\.config\sketchpad\server.log.
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$log = Join-Path $HOME ".config\sketchpad\server.log"
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
    & python.exe -u server.py helper --bind 127.0.0.1 2>&1 | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds 5
}
