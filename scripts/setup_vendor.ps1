param(
    [switch]$IncludeReferencePublisher
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$vendor = Join-Path $root "vendor"
New-Item -ItemType Directory -Force -Path $vendor | Out-Null

$spider = Join-Path $vendor "Spider_XHS"
if (-not (Test-Path $spider)) {
    git clone https://github.com/cv-cat/Spider_XHS.git $spider
}

if (Test-Path (Join-Path $spider "package.json")) {
    Push-Location $spider
    npm install
    Pop-Location
}

if ($IncludeReferencePublisher) {
    $publisher = Join-Path $vendor "xhs_ai_publisher"
    if (-not (Test-Path $publisher)) {
        git clone --depth 1 https://github.com/BetaStreetOmnis/xhs_ai_publisher.git $publisher
    }
}

Write-Host "Vendor setup complete."
