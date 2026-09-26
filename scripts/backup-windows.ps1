<#
.SYNOPSIS
    Backup / restore Redirector on Windows.

.DESCRIPTION
    A thin wrapper around app/utils/backup.py, so the web UI, the container
    entrypoint and this script cannot drift apart.

    Restores are applied immediately, which is only safe against a stopped
    service. If Redirector is running as a Windows service, stage the archive
    and restart instead:

        .\scripts\backup-windows.ps1 stage <archive.zip>
        Restart-Service Redirector

.EXAMPLE
    .\scripts\backup-windows.ps1 create "before 3.2"
    .\scripts\backup-windows.ps1 list
    .\scripts\backup-windows.ps1 verify  C:\backups\redirector-manual-....zip
    .\scripts\backup-windows.ps1 restore C:\backups\redirector-manual-....zip
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet('create', 'list', 'verify', 'stage', 'restore', 'paths', 'help')]
    [string]$Command = 'help',

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Argument
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

function Get-Python {
    $candidates = @()
    if ($env:REDIRECTOR_PYTHON) { $candidates += $env:REDIRECTOR_PYTHON }
    $candidates += @(
        (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
        (Join-Path $RepoRoot '.venv\bin\python')
    )
    $onPath = Get-Command python -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }

    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        if (-not (Test-Path $candidate)) { continue }
        # A bare `python` on PATH is often a Store stub or a bare interpreter
        # without the project's dependencies; importing flask is the real test.
        & $candidate -c 'import flask' 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }

    Write-Error @"
No usable Python interpreter found (flask is not importable).
Set one up first:
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
Or set REDIRECTOR_PYTHON to an existing environment.
"@
}

if ($Command -eq 'help') {
    Write-Host @"
Usage: .\scripts\backup-windows.ps1 <command> [argument]

  create [label]     Write a new .zip into the data directory's backups\ folder.
                     Safe to run while the service is up.
  list               List local archives with size, version and row counts.
  verify <archive>   Validate an archive without changing anything.
  stage <archive>    Park an archive to be applied on the next start. Use this
                     when the service is running.
  restore <archive>  Apply an archive NOW. Stop the service first:
                         Stop-Service Redirector
                         .\scripts\backup-windows.ps1 restore <archive>
                         Start-Service Redirector
  paths              Show the data directory, config file and resolved database.

Data directory: $RepoRoot\data   (override with REDIRECTOR_DATA_DIR)
Full detail:     docs\DATA-PERSISTENCE.md
"@
    exit 0
}

$python = Get-Python
$backupArgs = @('-m', 'app.utils.backup')

switch ($Command) {
    'create' {
        $backupArgs += 'create'
        if ($Argument -and $Argument[0]) { $backupArgs += @('--label', $Argument[0]) }
    }
    'list'   { $backupArgs += 'list' }
    'paths'  { $backupArgs += 'paths' }
    'verify' {
        if (-not $Argument -or -not $Argument[0]) { throw "verify needs an archive path." }
        $backupArgs += @('inspect', '--archive', $Argument[0])
    }
    'stage' {
        if (-not $Argument -or -not $Argument[0]) { throw "stage needs an archive path." }
        $backupArgs += @('stage', $Argument[0])
    }
    'restore' {
        if (-not $Argument -or -not $Argument[0]) { throw "restore needs an archive path." }
        $backupArgs += @('restore', $Argument[0])
    }
}

# The CLI prints its own UTF-8 output; make sure PowerShell does not mangle it.
$env:PYTHONIOENCODING = 'utf-8'
& $python @backupArgs
exit $LASTEXITCODE
