param(
    [string]$RepoZipUrl = "",
    [string]$TreeApiUrl = "",
    [string]$RawContentBaseUrl = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Branch = "music-gateway"
$VersionMarker = Join-Path $Root ".qkk-version"
$ManifestPath = Join-Path $Root ".qkk-update-manifest.json"
$BundledFfmpegPath = "assets/ffmpeg-win-x86_64-v7.1.exe"

function Set-Utf8NoBomContent {
    param(
        [string]$Path,
        [string]$Value
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Value, $encoding)
}

function Get-ShortRevision {
    param([string]$Value)
    $normalized = -join (($Value.Trim().ToCharArray() | Where-Object { [char]::IsLetterOrDigit($_) -or $_ -in @('.', '-', '_') }))
    if ($normalized.Length -ge 12 -and $normalized.Substring(0, 12) -match '^[0-9a-fA-F]{12}$') {
        return $normalized.Substring(0, 7)
    }
    return $normalized
}

function Get-RemoteRevisionId {
    try {
        $encodedBranch = [uri]::EscapeDataString($Branch)
        $commit = Invoke-RestMethod -Uri "https://api.github.com/repos/BloomingProsperity/Music/commits/$encodedBranch" -UseBasicParsing
        if ($commit.sha) {
            return Get-ShortRevision $commit.sha
        }
    }
    catch {
    }
    return (Get-Date).ToUniversalTime().ToString("yyyyMMddHHmmss")
}

function Get-DefaultTreeApiUrl {
    $encodedBranch = [uri]::EscapeDataString($Branch)
    return "https://api.github.com/repos/BloomingProsperity/Music/git/trees/$encodedBranch`?recursive=1"
}

function Get-DefaultRawContentBaseUrl {
    return "https://raw.githubusercontent.com/BloomingProsperity/Music/$Branch"
}

function ConvertTo-RawContentUrl {
    param(
        [string]$BaseUrl,
        [string]$RelativePath
    )
    $segments = $RelativePath -split "/"
    $encoded = $segments | ForEach-Object { [uri]::EscapeDataString($_) }
    return $BaseUrl.TrimEnd("/") + "/" + ($encoded -join "/")
}

function Test-IsExcludedPath {
    param([string]$RelativePath)
    $normalized = $RelativePath -replace "\\", "/"
    $excludedPrefixes = @(
        ".git/",
        ".venv/",
        ".pytest_cache/",
        "__pycache__/",
        "_log/",
        "_work/",
        "build/",
        "dist/",
        "output/",
        "plugins/"
    )
    foreach ($prefix in $excludedPrefixes) {
        if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $normalized -in @("config.json", ".qkk-version", ".qkk-update-manifest.json")
}

function Read-UpdateManifest {
    $files = @{}
    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        return $files
    }
    try {
        $manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($manifest.files) {
            $manifest.files.PSObject.Properties | ForEach-Object {
                $files[$_.Name] = [string]$_.Value
            }
        }
    }
    catch {
        return @{}
    }
    return $files
}

function Write-UpdateManifest {
    param(
        [string]$Revision,
        [hashtable]$Files
    )
    $orderedFiles = [ordered]@{}
    foreach ($key in ($Files.Keys | Sort-Object)) {
        $orderedFiles[$key] = $Files[$key]
    }
    $manifest = [ordered]@{
        branch = $Branch
        revision = $Revision
        files = $orderedFiles
    }
    Set-Utf8NoBomContent -Path $ManifestPath -Value (($manifest | ConvertTo-Json -Depth 5) + "`n")
}

function Invoke-IncrementalSourceUpdate {
    if (-not $TreeApiUrl) {
        $script:TreeApiUrl = Get-DefaultTreeApiUrl
    }
    if (-not $RawContentBaseUrl) {
        $script:RawContentBaseUrl = Get-DefaultRawContentBaseUrl
    }

    $tree = Invoke-RestMethod -Uri $TreeApiUrl -UseBasicParsing
    if (-not $tree.tree) {
        throw "Remote tree is empty."
    }

    $knownFiles = Read-UpdateManifest
    $nextFiles = @{}
    $downloaded = 0
    $skipped = 0

    foreach ($entry in $tree.tree) {
        if ([string]$entry.type -ne "blob") {
            continue
        }
        $relativePath = ([string]$entry.path) -replace "\\", "/"
        if (-not $relativePath -or (Test-IsExcludedPath $relativePath)) {
            continue
        }
        $remoteSha = [string]$entry.sha
        $nextFiles[$relativePath] = $remoteSha
        $localPath = Join-Path $Root ($relativePath -replace "/", [System.IO.Path]::DirectorySeparatorChar)

        if ($relativePath -ieq $BundledFfmpegPath -and (Test-Path -LiteralPath $localPath)) {
            $skipped += 1
            continue
        }
        if ((Test-Path -LiteralPath $localPath) -and $knownFiles.ContainsKey($relativePath) -and $knownFiles[$relativePath] -eq $remoteSha) {
            $skipped += 1
            continue
        }

        $parent = Split-Path -Parent $localPath
        if ($parent) {
            New-Item -ItemType Directory -Force -Path $parent | Out-Null
        }
        $rawUrl = ConvertTo-RawContentUrl -BaseUrl $RawContentBaseUrl -RelativePath $relativePath
        Invoke-WebRequest -Uri $rawUrl -OutFile $localPath -UseBasicParsing
        $downloaded += 1
    }

    $revision = Get-ShortRevision ([string]$tree.sha)
    if (-not $revision) {
        $revision = Get-RemoteRevisionId
    }
    Write-UpdateManifest -Revision $revision -Files $nextFiles
    Set-Utf8NoBomContent -Path $VersionMarker -Value ($revision + "`n")
    Write-Host "QKKDecrypt incremental update synced $downloaded file(s), skipped $skipped file(s)"
}

if (Test-Path -LiteralPath (Join-Path $Root ".git")) {
    git -C $Root pull --ff-only
    exit $LASTEXITCODE
}

try {
    Invoke-IncrementalSourceUpdate
    exit 0
}
catch {
    if (-not $RepoZipUrl -and -not $env:QKK_UPDATE_ZIP_URL) {
        Write-Error "Incremental update failed: $($_.Exception.Message)"
        exit 1
    }
    Write-Warning "Incremental update failed, falling back to archive update: $($_.Exception.Message)"
}

if (-not $RepoZipUrl) {
    $RepoZipUrl = $env:QKK_UPDATE_ZIP_URL
}
if (-not $RepoZipUrl) {
    $RepoZipUrl = "https://github.com/BloomingProsperity/Music/archive/refs/heads/music-gateway.zip"
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
    Set-Utf8NoBomContent -Path $VersionMarker -Value ((Get-RemoteRevisionId) + "`n")
}
finally {
    Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
}
