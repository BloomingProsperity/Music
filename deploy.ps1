param(
    [string]$InstallDir = "$env:USERPROFILE\QKKDecrypt",
    [string]$RepoZipUrl = "",
    [string]$TreeApiUrl = "",
    [string]$RawContentBaseUrl = "",
    [switch]$NoLaunch,
    [switch]$NoShortcut,
    [switch]$SkipDependencyInstall
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Branch = "music-gateway"
$BundledFfmpegPath = "assets/ffmpeg-win-x86_64-v7.1.exe"

function Write-Step {
    param([string]$Message)
    Write-Host "[QKK] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[QKK] $Message" -ForegroundColor Green
}

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

function Find-Python {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "py"; Args = @("-3") },
        @{ Exe = "python"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        $exe = $candidate.Exe
        $args = @($candidate.Args) + @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
        try {
            & $exe @args *> $null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
        }
    }

    throw "Python 3.10+ was not found. Install Python from https://www.python.org/downloads/windows/ and run this command again."
}

function Ensure-Python {
    try {
        return Find-Python
    }
    catch {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if ($null -eq $winget) {
            throw $_
        }

        Write-Step "Python 3.10+ was not found; installing Python 3.12 with winget"
        & winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            throw "winget could not install Python 3.12. Install Python 3.10+ manually and run this command again."
        }

        return Find-Python
    }
}

function Invoke-Python {
    param(
        [hashtable]$Python,
        [string[]]$ArgumentList
    )
    & $Python.Exe @($Python.Args) @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed: $($Python.Exe) $($ArgumentList -join ' ')"
    }
}

function New-DesktopShortcut {
    param([string]$TargetDir)

    $desktop = [Environment]::GetFolderPath("Desktop")
    if ([string]::IsNullOrWhiteSpace($desktop)) {
        return
    }

    $shortcutPath = Join-Path $desktop "QKKDecrypt UI.lnk"
    $runner = Join-Path $TargetDir "run-ui.ps1"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
    $shortcut.WorkingDirectory = $TargetDir
    $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll,138"
    $shortcut.Save()
}

function Get-RemoteRevisionId {
    param([string]$BranchName = $Branch)
    try {
        $encodedBranch = [uri]::EscapeDataString($BranchName)
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

function Test-IsExcludedManifestPath {
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
    param([string]$TargetDir)
    $manifestPath = Join-Path $TargetDir ".qkk-update-manifest.json"
    $files = @{}
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        return $files
    }
    try {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
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
        [string]$TargetDir,
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
    Set-Utf8NoBomContent -Path (Join-Path $TargetDir ".qkk-update-manifest.json") -Value (($manifest | ConvertTo-Json -Depth 5) + "`n")
}

function Write-UpdateManifestFromRemoteTree {
    param([string]$TargetDir)
    if (-not $TreeApiUrl) {
        $script:TreeApiUrl = Get-DefaultTreeApiUrl
    }
    try {
        $tree = Invoke-RestMethod -Uri $TreeApiUrl -UseBasicParsing
        if (-not $tree.tree) {
            return ""
        }
        $files = [ordered]@{}
        foreach ($entry in $tree.tree) {
            if ([string]$entry.type -ne "blob") {
                continue
            }
            $relativePath = ([string]$entry.path) -replace "\\", "/"
            if (-not $relativePath -or (Test-IsExcludedManifestPath $relativePath)) {
                continue
            }
            $files[$relativePath] = [string]$entry.sha
        }
        $revision = Get-ShortRevision ([string]$tree.sha)
        if (-not $revision) {
            $revision = Get-RemoteRevisionId
        }
        $manifest = [ordered]@{
            branch = $Branch
            revision = $revision
            files = $files
        }
        Set-Utf8NoBomContent -Path (Join-Path $TargetDir ".qkk-update-manifest.json") -Value (($manifest | ConvertTo-Json -Depth 5) + "`n")
        return $revision
    }
    catch {
        return ""
    }
}

function Sync-SourceTreeFromRemoteTree {
    param([string]$TargetDir)
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

    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    $knownFiles = Read-UpdateManifest -TargetDir $TargetDir
    $nextFiles = @{}
    $downloaded = 0
    $skipped = 0

    foreach ($entry in $tree.tree) {
        if ([string]$entry.type -ne "blob") {
            continue
        }
        $relativePath = ([string]$entry.path) -replace "\\", "/"
        if (-not $relativePath -or (Test-IsExcludedManifestPath $relativePath)) {
            continue
        }
        $remoteSha = [string]$entry.sha
        $nextFiles[$relativePath] = $remoteSha
        $localPath = Join-Path $TargetDir ($relativePath -replace "/", [System.IO.Path]::DirectorySeparatorChar)

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
    Write-UpdateManifest -TargetDir $TargetDir -Revision $revision -Files $nextFiles
    Write-Host "QKKDecrypt source sync downloaded $downloaded file(s), skipped $skipped file(s)"
    return $revision
}

$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

try {
    Write-Step "Installing to $InstallDir"

    Write-Step "Syncing source files"
    $Revision = Sync-SourceTreeFromRemoteTree -TargetDir $InstallDir
    if (-not $Revision) {
        $Revision = Get-RemoteRevisionId
    }
    Set-Utf8NoBomContent -Path (Join-Path $InstallDir ".qkk-version") -Value ($Revision + "`n")

    $VenvDir = Join-Path $InstallDir ".venv"
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

    if (-not $SkipDependencyInstall) {
        $Python = Ensure-Python
        if (-not (Test-Path $VenvPython)) {
            Write-Step "Creating virtual environment"
            Invoke-Python -Python $Python -ArgumentList @("-m", "venv", $VenvDir)
        }

        Write-Step "Installing Python dependencies"
        & $VenvPython -m pip install --upgrade pip
        if ($LASTEXITCODE -ne 0) {
            throw "pip upgrade failed"
        }
        & $VenvPython -m pip install -r (Join-Path $InstallDir "requirements.txt")
        if ($LASTEXITCODE -ne 0) {
            throw "dependency installation failed"
        }
    }
    else {
        Write-Step "Skipping Python dependency installation"
    }

    $Ffmpeg = Join-Path $InstallDir "assets\ffmpeg-win-x86_64-v7.1.exe"
    if (-not (Test-Path $Ffmpeg)) {
        throw "Bundled ffmpeg is missing at $Ffmpeg"
    }

    if (-not $NoShortcut) {
        Write-Step "Creating desktop shortcut"
        New-DesktopShortcut -TargetDir $InstallDir
    }

    Write-Ok "Install complete"
    Write-Host "Install path: $InstallDir"
    Write-Host "Run later: powershell -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\run-ui.ps1`""

    if (-not $NoLaunch) {
        if ($SkipDependencyInstall -and -not (Test-Path $VenvPython)) {
            throw "Cannot launch UI because dependency installation was skipped."
        }
        Write-Step "Starting UI"
        Start-Process -FilePath $VenvPython -ArgumentList "`"$InstallDir\ui_main.py`"" -WorkingDirectory $InstallDir
    }
}
catch {
    throw
}
