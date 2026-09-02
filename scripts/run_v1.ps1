param(
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "run_backend.ps1"
& $launcher -HostAddress $HostAddress -Port $Port -NoReload
