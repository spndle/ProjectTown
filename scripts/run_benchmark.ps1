param(
    [ValidateSet("smoke", "formal")]
    [string]$Profile = "formal",
    [string]$Output = "benchmark/results/formal-v1.0",
    [ValidateRange(0, 2147483647)]
    [int]$Seed = 1729
)

$ErrorActionPreference = "Stop"
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project virtual environment was not found. Install requirements-dev.txt first."
}

Push-Location $projectRoot
try {
    & $python -m backend.app.v1.evaluation --output $Output --profile $Profile --seed $Seed
    if ($LASTEXITCODE -ne 0) {
        throw "Benchmark exited with code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
