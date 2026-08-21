[CmdletBinding()]
param(
    [string]$Distribution = "Ubuntu",
    [ValidatePattern('^[1-9][0-9]*(mb|gb)$')]
    [string]$MaxMemory = "1gb",
    [ValidateRange(1024, 65535)]
    [int]$Port = 6380,
    [ValidatePattern('^$|^[A-Za-z0-9_-]{32,128}$')]
    [string]$Password = ""
)

$ErrorActionPreference = "Stop"
$ServiceName = "library-of-context-redis"
$ConfigDirectory = "/etc/library-of-context"
$ConfigPath = "$ConfigDirectory/redis.conf"
$SecretPath = "$ConfigDirectory/redis.secret"
$UnitPath = "/etc/systemd/system/${ServiceName}.service"

function Invoke-WslChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = & wsl @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "WSL command failed with exit code ${LASTEXITCODE}: wsl $($Arguments -join ' ')"
    }
    return $output
}

function Set-WslFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content
    )
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($Content)
    $encoded = [Convert]::ToBase64String($bytes)
    Invoke-WslChecked -Arguments @(
        "-d", $Distribution, "-u", "root", "--", "sh", "-lc",
        "umask 077; printf '%s' '$encoded' | base64 -d > '$Path'"
    ) | Out-Null
}

Write-Host "Checking WSL distribution '$Distribution'..."
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "--", "sh", "-lc",
    'test "$(ps -p 1 -o comm=)" = systemd'
) | Out-Null

Write-Host "Installing the Redis package..."
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "apt-get", "update"
) | Out-Null
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "apt-get", "install", "-y",
    "--no-install-recommends", "redis-server"
) | Out-Null

if (-not $Password) {
    $storedPassword = Invoke-WslChecked -Arguments @(
        "-d", $Distribution, "-u", "root", "--", "sh", "-lc",
        "if test -f '$SecretPath'; then cat '$SecretPath'; fi"
    )
    $Password = (@($storedPassword) -join "").Trim()
}
if (-not $Password) {
    $randomBytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($randomBytes)
    }
    finally {
        $generator.Dispose()
    }
    $Password = -join ($randomBytes | ForEach-Object { $_.ToString("x2") })
}
if ($Password -notmatch '^[A-Za-z0-9_-]{32,128}$') {
    throw "The Redis password must contain 32 to 128 letters, digits, underscores, or hyphens."
}

$redisConfig = @"
bind 127.0.0.1 ::1
protected-mode yes
port $Port
daemonize no
supervised systemd
logfile ""
dir /var/lib/$ServiceName
save ""
appendonly no
maxmemory $MaxMemory
maxmemory-policy allkeys-lfu
requirepass $Password
rename-command CONFIG ""
rename-command FLUSHALL ""
rename-command FLUSHDB ""
"@

$serviceUnit = @"
[Unit]
Description=Library of Context disposable Redis cache
After=network.target

[Service]
Type=notify
User=redis
Group=redis
ExecStart=/usr/bin/redis-server $ConfigPath --supervised systemd
KillSignal=SIGTERM
TimeoutStopSec=10
Restart=on-failure
RestartSec=2
RuntimeDirectory=$ServiceName
RuntimeDirectoryMode=0700
StateDirectory=$ServiceName
StateDirectoryMode=0700
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict

[Install]
WantedBy=multi-user.target
"@

Write-Host "Writing the dedicated Library Redis configuration..."
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "install", "-d", "-m", "0750",
    "-o", "root", "-g", "redis", $ConfigDirectory
) | Out-Null
Set-WslFile -Path $ConfigPath -Content $redisConfig
Set-WslFile -Path $SecretPath -Content $Password
Set-WslFile -Path $UnitPath -Content $serviceUnit
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "chown", "root:redis", $ConfigPath
) | Out-Null
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "chmod", "0640", $ConfigPath
) | Out-Null
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "chmod", "0600", $SecretPath
) | Out-Null
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "chmod", "0644", $UnitPath
) | Out-Null

Write-Host "Starting the dedicated Library Redis service..."
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "systemctl", "daemon-reload"
) | Out-Null
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "-u", "root", "--", "systemctl", "enable", "--now",
    "${ServiceName}.service"
) | Out-Null
Invoke-WslChecked -Arguments @(
    "-d", $Distribution, "--", "redis-cli", "-h", "127.0.0.1", "-p",
    "$Port", "-a", $Password, "--no-auth-warning", "PING"
) | Out-Null

$redisUrl = "redis://:${Password}@127.0.0.1:${Port}/0"
Write-Host "Library Redis is ready. It does not modify Ubuntu's default Redis service."
Write-Host "Set this value in the PowerShell session that runs the Library:"
Write-Host "`$env:LIBRARY_OF_CONTEXT_REDIS_URL = '$redisUrl'"
