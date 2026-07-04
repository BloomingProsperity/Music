param(
    [string]$RepoZipUrl = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path

if (Test-Path -LiteralPath (Join-Path $Root ".git")) {
    git -C $Root pull --ff-only
    exit $LASTEXITCODE
}

if (-not $RepoZipUrl) {
    $RepoZipUrl = $env:QKK_UPDATE_ZIP_URL
}
if (-not $RepoZipUrl) {
    $RepoZipUrl = "https://github.com/BloomingProsperity/Music/archive/refs/heads/codex/platforms-ncm-kuwo-upgrade.zip"
}

$TempRoot = Join-Path $env:TEMP ("qkk-update-" + [guid]::NewGuid().ToString("N"))
$ZipPath = Join-Path $TempRoot "repo.zip"
New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null

try {
    Invoke-WebRequest -Uri $RepoZipUrl -OutFile $ZipPath
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $TempRoot -Force
    $SourceRoot = Get-ChildItem -LiteralPath $TempRoot -Directory |
        Where-Object { $_.Name -ne "__MACOSX" } |
        Select-Object -First 1
    if (-not $SourceRoot) {
        throw "Downloaded archive did not contain a project directory."
    }

    $Exclude = @(
        ".git",
        ".venv",
        ".pytest_cache",
        "__pycache__",
        "_log",
        "_work",
        "build",
        "dist",
        "output",
        "plugins"
    )

    Get-ChildItem -LiteralPath $SourceRoot.FullName -Force |
        Where-Object { $Exclude -notcontains $_.Name } |
        ForEach-Object {
            Copy-Item -LiteralPath $_.FullName -Destination $Root -Recurse -Force
        }

    Write-Host "QKKDecrypt update copied from $RepoZipUrl"
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
