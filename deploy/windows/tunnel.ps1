# Windows twin of deploy/tunnel.sh: keeps hub:127.0.0.1:<HubPort> -> this machine's helper
# (127.0.0.1:8791), reconnecting forever. Run hidden at logon by install.ps1.
param(
    [string]$Hub = "archbox",   # ssh host alias with key login (in ~/.ssh/config)
    [int]$HubPort = 8792        # the hub's port for this machine; another helper may own 8791
)
$ssh = @("-o", "BatchMode=yes", "-o", "ConnectTimeout=10")
# A tunnel that died without a clean close (sleep, network switch) leaves the hub's sshd
# holding the port, so free it before reconnecting. No double quotes: PowerShell 5.1 mangles
# them on the way to ssh.exe ($pid is digits or empty, so it needs none).
$free = 'pid=$(ss -Hltnp ''sport = :' + $HubPort + ''' | grep -o ''pid=[0-9]*'' | head -1 | cut -d= -f2); [ -z $pid ] || kill $pid; true'
while ($true) {
    & ssh @ssh $Hub $free
    & ssh @ssh -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes `
        -R "127.0.0.1:${HubPort}:127.0.0.1:8791" $Hub
    Start-Sleep -Seconds 10
}
