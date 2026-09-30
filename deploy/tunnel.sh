#!/bin/sh
# Keeps hub:127.0.0.1:8791 -> this machine's helper (127.0.0.1:8791).
# A tunnel that died without a clean close (sleep, network switch) leaves the hub's sshd
# holding the port for hours, so free it before reconnecting.
HUB="${SKETCHPAD_HUB:?set SKETCHPAD_HUB to the hub's ssh host alias}"
SSH="ssh -o BatchMode=yes -o ControlMaster=no -o ControlPath=none -o ConnectTimeout=10"
$SSH "$HUB" 'pid=$(ss -Hltnp "sport = :8791" | grep -o "pid=[0-9]*" | head -1 | cut -d= -f2); [ -n "$pid" ] && kill "$pid"; true'
exec $SSH -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
  -R 127.0.0.1:8791:127.0.0.1:8791 "$HUB"
