param(
    [string]$InstallDir = "$env:USERPROFILE\QKKDecrypt",
    [string]$RepoZipUrl = "https://github.com/BloomingProsperity/Music/archive/refs/heads/codex/one-click-deploy.zip",
    [switch]$NoLaunch,
    [switch]$NoShortcut
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step {
    param([string]$Message)
    Write-Host "[QKK] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[QKK] $Message" -ForegroundColor Green
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

function Copy-SourceTree {
    param(
        [string]$SourceDir,
        [string]$TargetDir
    )

    New-Item -ItemType Directory -Force -Path $TargetDir | Out-Null
    $excludeDirs = @(".git", ".venv", "build", "dist", "_log", "_output", "__pycache__", ".pytest_cache")
    $excludeFiles = @("config.json")
    $args = @(
        $SourceDir,
        $TargetDir,
        "/MIR",
        "/FFT",
        "/R:2",
        "/W:2",
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/XD"
    ) + $excludeDirs + @("/XF") + $excludeFiles

    & robocopy @args | Out-Null
    if ($LASTEXITCODE -gt 7) {
        throw "Failed to copy source files into $TargetDir"
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

$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)
$TempRoot = Join-Path $env:TEMP ("qkkdeploy-" + [guid]::NewGuid().ToString("N"))
$ZipPath = Join-Path $TempRoot "source.zip"
$ExtractDir = Join-Path $TempRoot "source"

try {
    Write-Step "Installing to $InstallDir"
    New-Item -ItemType Directory -Force -Path $TempRoot | Out-Null

    Write-Step "Downloading source package"
    Invoke-WebRequest -Uri $RepoZipUrl -OutFile $ZipPath -UseBasicParsing

    Write-Step "Extracting source package"
    Expand-Archive -Path $ZipPath -DestinationPath $ExtractDir -Force
    $SourceRoot = Get-ChildItem -Path $ExtractDir -Directory | Select-Object -First 1
    if ($null -eq $SourceRoot) {
        throw "Downloaded package did not contain a source directory."
    }

    Write-Step "Syncing source files"
    Copy-SourceTree -SourceDir $SourceRoot.FullName -TargetDir $InstallDir

    $Python = Ensure-Python
    $VenvDir = Join-Path $InstallDir ".venv"
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"

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
        Write-Step "Starting UI"
        Start-Process -FilePath $VenvPython -ArgumentList "`"$InstallDir\ui_main.py`"" -WorkingDirectory $InstallDir
    }
}
finally {
    if (Test-Path $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
