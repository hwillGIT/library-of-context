[CmdletBinding()]
param(
    [string]$Distribution = "Ubuntu",
    [ValidatePattern('^[1-9][0-9]*(mb|gb)$')]
    [string]$MaxMemory = "4gb"
)

$ErrorActionPreference = "Stop"

function Invoke-WslChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & wsl @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code ${LASTEXITCODE}: wsl $($Arguments -join ' ')"
    }
}

Write-Host "Installing Redis in WSL distribution '$Distribution'..."
Invoke-WslChecked -Arguments @("-d", $Distribution, "-u", "root", "--", "apt-get", "update")
Invoke-WslChecked -Arguments @("-d", $Distribution, "-u", "root", "--", "apt-get", "install", "-y", "redis-server")

Write-Host "Enabling the local Redis service..."
Invoke-WslChecked -Arguments @("-d", $Distribution, "-u", "root", "--", "systemctl", "enable", "--now", "redis-server")

Write-Host "Applying disposable-cache housekeeping settings..."
Invoke-WslChecked -Arguments @("-d", $Distribution, "--", "redis-cli", "CONFIG", "SET", "maxmemory", $MaxMemory)
Invoke-WslChecked -Arguments @("-d", $Distribution, "--", "redis-cli", "CONFIG", "SET", "maxmemory-policy", "allkeys-lfu")
Invoke-WslChecked -Arguments @("-d", $Distribution, "--", "redis-cli", "CONFIG", "SET", "appendonly", "no")
Invoke-WslChecked -Arguments @("-d", $Distribution, "--", "sh", "-lc", "redis-cli CONFIG SET save ''")
Invoke-WslChecked -Arguments @("-d", $Distribution, "--", "redis-cli", "CONFIG", "REWRITE")

Write-Host "Verifying Redis..."
Invoke-WslChecked -Arguments @("-d", $Distribution, "--", "redis-cli", "PING")
Write-Host "Library Redis is ready at redis://127.0.0.1:6379/0 (no Docker or cloud account required)."
