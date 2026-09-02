param(
    [Parameter(Mandatory = $true)]
    [string]$GodotPath,
    [string]$ValidationRoot = "sandbox/tmp/godot-v1-validation",
    [ValidateRange(1, 65535)]
    [int]$Port = 18766
)

$ErrorActionPreference = "Stop"
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$allowedRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "sandbox/tmp"))
$validationPath = if ([System.IO.Path]::IsPathRooted($ValidationRoot)) {
    [System.IO.Path]::GetFullPath($ValidationRoot)
}
else {
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot $ValidationRoot))
}
$allowedPrefix = $allowedRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $validationPath.StartsWith($allowedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "ValidationRoot must be a child of $allowedRoot"
}
if (Test-Path -LiteralPath $validationPath) {
    throw "ValidationRoot already exists: $validationPath"
}

$godotExecutable = (Resolve-Path -LiteralPath $GodotPath -ErrorAction Stop).Path
if (-not (Test-Path -LiteralPath $godotExecutable -PathType Leaf)) {
    throw "Godot executable is not a file: $godotExecutable"
}
$pythonExecutable = Join-Path $projectRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $pythonExecutable -PathType Leaf)) {
    throw "Project virtual environment is missing: $pythonExecutable"
}

New-Item -ItemType Directory -Path $validationPath -ErrorAction Stop | Out-Null
$runtimeSandbox = Join-Path $validationPath "sandbox"
New-Item -ItemType Directory -Path $runtimeSandbox -ErrorAction Stop | Out-Null
$stdoutPath = Join-Path $validationPath "uvicorn.stdout.log"
$stderrPath = Join-Path $validationPath "uvicorn.stderr.log"
$editorStdoutPath = Join-Path $validationPath "godot-editor.stdout.log"
$editorStderrPath = Join-Path $validationPath "godot-editor.stderr.log"
$sceneStdoutPath = Join-Path $validationPath "godot-scene.stdout.log"
$sceneStderrPath = Join-Path $validationPath "godot-scene.stderr.log"
$smokeStdoutPath = Join-Path $validationPath "godot-api-smoke.stdout.log"
$smokeStderrPath = Join-Path $validationPath "godot-api-smoke.stderr.log"
$testUrl = "http://127.0.0.1:$Port"

function Assert-GodotRun {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Name,
        [string]$StandardOutputPath,
        [string]$StandardErrorPath
    )
    if ($Process.ExitCode -ne 0) {
        throw "$Name failed with exit code $($Process.ExitCode)"
    }
    $combined = ""
    if (Test-Path -LiteralPath $StandardOutputPath -PathType Leaf) {
        $combined += Get-Content -LiteralPath $StandardOutputPath -Raw -Encoding utf8
    }
    if (Test-Path -LiteralPath $StandardErrorPath -PathType Leaf) {
        $combined += Get-Content -LiteralPath $StandardErrorPath -Raw -Encoding utf8
    }
    if ($combined -match "(?im)(SCRIPT ERROR|Parse Error|Failed to load script)") {
        throw "$Name reported a script loading error"
    }
}

$previousDatabasePath = $env:PROJECTTOWN_DATABASE_PATH
$previousSandboxRoot = $env:PROJECTTOWN_SANDBOX_ROOT
$previousTestUrl = $env:PROJECTTOWN_TEST_URL
$server = $null
try {
    $env:PROJECTTOWN_DATABASE_PATH = Join-Path $validationPath "projecttown.db"
    $env:PROJECTTOWN_SANDBOX_ROOT = $runtimeSandbox
    $env:PROJECTTOWN_TEST_URL = $testUrl

    $godotProject = Join-Path $projectRoot "godot"
    $editorProcess = Start-Process `
        -FilePath $godotExecutable `
        -ArgumentList @("--headless", "--editor", "--path", $godotProject, "--quit") `
        -RedirectStandardOutput $editorStdoutPath `
        -RedirectStandardError $editorStderrPath `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    Assert-GodotRun $editorProcess "Godot editor load" $editorStdoutPath $editorStderrPath

    $sceneProcess = Start-Process `
        -FilePath $godotExecutable `
        -ArgumentList @("--headless", "--path", $godotProject, "--quit-after", "20") `
        -RedirectStandardOutput $sceneStdoutPath `
        -RedirectStandardError $sceneStderrPath `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    Assert-GodotRun $sceneProcess "Godot main-scene smoke" $sceneStdoutPath $sceneStderrPath

    $server = Start-Process `
        -FilePath $pythonExecutable `
        -ArgumentList @("-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", $Port.ToString()) `
        -WorkingDirectory $projectRoot `
        -RedirectStandardOutput $stdoutPath `
        -RedirectStandardError $stderrPath `
        -WindowStyle Hidden `
        -PassThru

    $ready = $false
    for ($attempt = 0; $attempt -lt 100; $attempt++) {
        if ($server.HasExited) {
            throw "Uvicorn exited before readiness with code $($server.ExitCode)"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri "$testUrl/health" -TimeoutSec 1
            if ($response.StatusCode -eq 200) {
                $ready = $true
                break
            }
        }
        catch {
            # The listener may not be ready during the bounded startup window.
        }
        Start-Sleep -Milliseconds 100
    }
    if (-not $ready) {
        throw "Uvicorn did not become ready at $testUrl"
    }

    $smokeProcess = Start-Process `
        -FilePath $godotExecutable `
        -ArgumentList @("--headless", "--path", $godotProject, "--script", "res://tests/api_smoke.gd") `
        -RedirectStandardOutput $smokeStdoutPath `
        -RedirectStandardError $smokeStderrPath `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    Assert-GodotRun $smokeProcess "Godot API smoke" $smokeStdoutPath $smokeStderrPath
    $smokeOutput = Get-Content -LiteralPath $smokeStdoutPath -Raw -Encoding utf8
    if ($smokeOutput -notmatch "GODOT_API_SMOKE_OK") {
        throw "Godot API smoke did not emit its success marker"
    }
    Write-Output ($smokeOutput.Trim())

    $questListing = Invoke-RestMethod -Uri "$testUrl/api/v2/quests"
    $quests = @($questListing.items)
    if ($quests.Count -ne 1) {
        throw "Expected one smoke Quest, got $($quests.Count)"
    }
    $quest = $quests[0]
    $eventListing = Invoke-RestMethod -Uri "$testUrl/api/v2/quests/$($quest.id)/events"
    $evidenceListing = Invoke-RestMethod -Uri "$testUrl/api/v2/quests/$($quest.id)/evidence"
    $events = @($eventListing.items)
    $evidence = @($evidenceListing.items)
    if ($quest.status -ne "completed") {
        throw "Smoke Quest ended as $($quest.status)"
    }
    Write-Output "LIVE_GODOT_BACKEND_OK quest=$($quest.id) status=$($quest.status) events=$($events.Count) evidence=$($evidence.Count)"
    Write-Output "GODOT_VALIDATION_ROOT=$validationPath"
}
finally {
    if ($null -ne $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -ErrorAction Stop
        $server.WaitForExit()
    }
    $env:PROJECTTOWN_DATABASE_PATH = $previousDatabasePath
    $env:PROJECTTOWN_SANDBOX_ROOT = $previousSandboxRoot
    $env:PROJECTTOWN_TEST_URL = $previousTestUrl
}
