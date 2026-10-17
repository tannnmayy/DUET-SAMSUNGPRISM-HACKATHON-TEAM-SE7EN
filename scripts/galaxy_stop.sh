#!/usr/bin/env bash
# Stop everything scripts/galaxy_demo.sh started: the agent, the token server and the fast mind.
# (On Windows, closing or interrupting galaxy_demo.sh can leave them running.)
#
#   bash scripts/galaxy_stop.sh
if command -v powershell >/dev/null 2>&1; then
  powershell -NoProfile -Command "\$p = @(Get-CimInstance Win32_Process | Where-Object { \$_.ProcessId -ne \$PID -and \$_.CommandLine -match 'duet_voice\.galaxy\.(agent|server)|llama-server.*--alias gemma-4-e2b' }); \$p | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }; Write-Output ('stopped ' + \$p.Count + ' process(es)')"
else
  pkill -f 'duet_voice.galaxy.(agent|server)' ; pkill -f 'llama-server.*--alias gemma-4-e2b' ; echo stopped
fi
