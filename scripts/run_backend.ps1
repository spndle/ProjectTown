param(
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8000,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $pythonExecutable = $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonCommand) {
        throw "Python was not found. Install Python 3.11 or newer first."
    }
    $pythonExecutable = $pythonCommand.Source
}

$pythonVersion = & $pythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to read the Python version."
}

$versionParts = $pythonVersion.Trim().Split('.')
if ([int]$versionParts[0] -lt 3 -or ([int]$versionParts[0] -eq 3 -and [int]$versionParts[1] -lt 11)) {
    throw "ProjectTown requires Python 3.11+. Current version: $pythonVersion."
}

Push-Location $projectRoot
try {
    & $pythonExecutable -c "import fastapi, uvicorn" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Backend dependencies are missing. Run: $pythonExecutable -m pip install -r requirements-dev.txt"
    }

    $uvicornArguments = @(
        "-m", "uvicorn", "backend.main:app",
        "--host", $HostAddress,
        "--port", $Port.ToString(),
        "--no-proxy-headers"
    )
    if (Test-Path -LiteralPath ".env" -PathType Leaf) {
        $uvicornArguments += @("--env-file", ".env")
    }
    if (-not $NoReload) {
        $uvicornArguments += "--reload"
    }

    Write-Host "ProjectTown API: http://${HostAddress}:$Port"
    Write-Host "Interactive API docs: http://${HostAddress}:$Port/docs"
    & $pythonExecutable @uvicornArguments
    if ($LASTEXITCODE -ne 0) {
        throw "ProjectTown backend exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
