# Runs the sketchpad helper and starts it again if it ever exits. Run hidden at logon by
# install.ps1; output goes to ~\.config\sketchpad\server.log.
$repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$log = Join-Path $HOME ".config\sketchpad\server.log"
Set-Location $repo
while ($true) {
    & python.exe -u server.py helper --bind 127.0.0.1 2>&1 | Out-File -Append -Encoding utf8 $log
    Start-Sleep -Seconds 5
}
