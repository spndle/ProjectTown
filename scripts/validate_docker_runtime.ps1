[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$DefaultsFile,
    [Parameter(Mandatory)]
    [string]$EvidenceRoot,
    [Parameter(Mandatory)]
    [string]$ProjectName,
    [Parameter(Mandatory)]
    [string]$Image,
    [Parameter(Mandatory)]
    [ValidateRange(1024, 65535)]
    [int]$HostPort
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$baseCompose = Join-Path $repoRoot "docker-compose.yml"
$validationCompose = Join-Path $repoRoot "docker-compose.validation.yml"
$defaults = [System.IO.Path]::GetFullPath($DefaultsFile)
$evidence = [System.IO.Path]::GetFullPath($EvidenceRoot)

function Write-CreateOnlyText {
    param([Parameter(Mandatory)][string]$Path, [Parameter(Mandatory)][string]$Text)
    $writer = [System.IO.StreamWriter]::new(
        [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write),
        [System.Text.UTF8Encoding]::new($false)
    )
    try { $writer.Write($Text) } finally { $writer.Dispose() }
}

function Invoke-ComposeEvidence {
    param([Parameter(Mandatory)][string]$Name, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    $output = & docker compose --env-file $defaults --project-name $ProjectName -f $baseCompose -f $validationCompose @Arguments 2>&1 | Out-String
    $exitCode = $LASTEXITCODE
    Write-CreateOnlyText (Join-Path $evidence ("$Name.log")) $output
    if ($exitCode -ne 0) { throw "Compose command '$Name' failed with exit code $exitCode." }
    return $output
}

function Wait-ContainerHealthy {
    param(
        [Parameter(Mandatory)][string]$ContainerId,
        [Parameter(Mandatory)][string]$EvidenceName,
        [int]$TimeoutSeconds = 60
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $health = (& docker inspect --format '{{.State.Health.Status}}' $ContainerId 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw "Unable to inspect container health." }
        if ($health -eq "healthy") {
            Write-CreateOnlyText (Join-Path $evidence $EvidenceName) $health
            return
        }
        if ($health -eq "unhealthy") { throw "Container became unhealthy." }
        Start-Sleep -Seconds 2
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "Container health did not become healthy within $TimeoutSeconds seconds."
}

if (-not (Test-Path -LiteralPath $defaults -PathType Leaf)) { throw "DefaultsFile must be an existing file." }
if ((Split-Path -Leaf $defaults) -like ".env*") { throw "DefaultsFile must be a non-dot validation defaults file." }
if (Test-Path -LiteralPath $evidence) { throw "EvidenceRoot must be fresh (create-only)." }
if ($ProjectName -notmatch "^projecttown-validation-[a-z0-9-]+$") { throw "ProjectName must be a unique projecttown-validation-* identifier." }
if ([string]::IsNullOrWhiteSpace($Image)) { throw "Image must name an existing local image." }

New-Item -ItemType Directory -Path $evidence -ErrorAction Stop | Out-Null
$started = [DateTimeOffset]::UtcNow.ToString("o")
Write-CreateOnlyText (Join-Path $evidence "command-contract.txt") @"
defaults_file=$defaults
project_name=$ProjectName
image=$Image
host_port=$HostPort
started_utc=$started
policy=explicit --env-file; local image only; no build; no pull; no .env; no volumes removal
"@

try {
    $null = & docker image inspect $Image 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Image must already exist locally; no pull or build is permitted." }
    $env:PROJECTTOWN_VALIDATION_IMAGE = $Image
    $env:PROJECTTOWN_VALIDATION_PORT = [string]$HostPort
    Invoke-ComposeEvidence "compose-config" config --no-interpolate | Out-Null
    Invoke-ComposeEvidence "compose-up" up --detach --no-build --pull never | Out-Null
    $containerId = (Invoke-ComposeEvidence "compose-ps" ps -q projecttown).Trim()
    if ([string]::IsNullOrWhiteSpace($containerId)) { throw "Compose did not return a projecttown container id." }
    Wait-ContainerHealthy $containerId "health-before-restart.txt"
    Invoke-ComposeEvidence "compose-restart" restart projecttown | Out-Null
    Wait-ContainerHealthy $containerId "health-after-restart.txt"
    Invoke-ComposeEvidence "sqlite-user-version" exec -T projecttown python -c "import sqlite3; connection = sqlite3.connect('/app/data/projecttown.db'); print(connection.execute('PRAGMA user_version').fetchone()[0])" | Out-Null
    Invoke-ComposeEvidence "compose-logs" logs --no-color --tail 200 projecttown | Out-Null
}
finally {
    if (Test-Path -LiteralPath $evidence) {
        try { Invoke-ComposeEvidence "compose-down" down --remove-orphans | Out-Null }
        catch { Write-CreateOnlyText (Join-Path $evidence "compose-down-failure.txt") $_.Exception.Message }
    }
}
