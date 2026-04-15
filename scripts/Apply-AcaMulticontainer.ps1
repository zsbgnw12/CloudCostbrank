#Requires -Version 5.1
<#
.SYNOPSIS
  Export cloudcost-brank, patch to API + Celery worker + beat (1 replica), apply via az.

.DESCRIPTION
  Does not store secrets in the repo. Requires: Azure CLI, Python 3.11+, PyYAML
  ( pip install -r scripts/requirements-aca.txt )

.PARAMETER ResourceGroup
.PARAMETER Name
.PARAMETER WhatIf
  Only write patched YAML to a temp file path and print it; do not run az update.
#>
param(
    [string] $ResourceGroup = "CloudCost",
    [string] $Name = "cloudcost-brank",
    [switch] $WhatIf
)

$ErrorActionPreference = "Stop"
$cloudcostRoot = Split-Path -Parent $PSScriptRoot
Set-Location $cloudcostRoot

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $py) {
    Write-Error "Python not found on PATH."
}

& $py.Source -c "import yaml" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyYAML..." -ForegroundColor Yellow
    & $py.Source -m pip install -q -r (Join-Path $cloudcostRoot "scripts\requirements-aca.txt")
}

$patchFile = [System.IO.Path]::GetTempFileName() + ".yaml"
try {
    az containerapp show -g $ResourceGroup -n $Name -o json | & $py.Source (Join-Path $cloudcostRoot "scripts\aca_apply_multicontainer.py") | Set-Content -Path $patchFile -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "az show or patch script failed" }

    if ($WhatIf) {
        Write-Host "Patched YAML written to: $patchFile"
        Get-Content $patchFile -TotalCount 40
        Write-Host "..."
        return
    }

    az containerapp update -g $ResourceGroup -n $Name --yaml $patchFile
    if ($LASTEXITCODE -ne 0) { throw "az containerapp update failed" }

    Write-Host "Update submitted. Check revision: az containerapp revision list -g $ResourceGroup -n $Name -o table"
}
finally {
    if (-not $WhatIf -and (Test-Path $patchFile)) {
        Remove-Item $patchFile -Force -ErrorAction SilentlyContinue
    } elseif ($WhatIf -and (Test-Path $patchFile)) {
        Write-Host "(WhatIf) Patched file kept at: $patchFile" -ForegroundColor Cyan
    }
}
