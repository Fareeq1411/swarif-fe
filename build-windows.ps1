param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$BuildWorkPath = $null

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Find-Python312 {
    $Candidates = @(
        "$env:LocalAppData\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python312\python.exe"
    )
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path $Candidate)) {
            return $Candidate
        }
    }

    $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonCommand -and $PythonCommand.Source -notmatch "\\WindowsApps\\") {
        try {
            $Version = & $PythonCommand.Source -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
            if ($LASTEXITCODE -eq 0 -and $Version -eq "3.12") {
                return $PythonCommand.Source
            }
        }
        catch {
            # Ignore broken aliases/installations and continue to automatic setup.
        }
    }

    $Launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Launcher) {
        try {
            $Resolved = & $Launcher.Source -3.12 -c "import sys; print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $Resolved -and (Test-Path $Resolved)) {
                return $Resolved.Trim()
            }
        }
        catch {
            # The launcher can exist even when no matching Python is installed.
        }
    }
    return $null
}

function Install-Python312 {
    Write-Step "Python 3.12 is not installed; installing it"
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($Winget) {
        & $Winget.Source install --exact --id Python.Python.3.12 --scope user `
            --accept-package-agreements --accept-source-agreements --silent
        if ($LASTEXITCODE -ne 0) {
            throw "winget could not install Python 3.12 (exit code $LASTEXITCODE)."
        }
        return
    }

    $Architecture = $env:PROCESSOR_ARCHITECTURE
    if ($Architecture -eq "AMD64") {
        $InstallerName = "python-3.12.10-amd64.exe"
    } elseif ($Architecture -eq "ARM64") {
        $InstallerName = "python-3.12.10-arm64.exe"
    } else {
        throw "Unsupported Windows architecture: $Architecture. Install 64-bit Python 3.12 manually."
    }

    $InstallerUrl = "https://www.python.org/ftp/python/3.12.10/$InstallerName"
    $InstallerPath = Join-Path $env:TEMP $InstallerName
    Write-Host "Downloading Python from $InstallerUrl"
    Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath -UseBasicParsing

    $Signature = Get-AuthenticodeSignature $InstallerPath
    if ($Signature.Status -ne "Valid" -or $Signature.SignerCertificate.Subject -notmatch "Python Software Foundation") {
        Remove-Item $InstallerPath -Force -ErrorAction SilentlyContinue
        throw "The downloaded Python installer did not have a valid Python Software Foundation signature."
    }

    $Process = Start-Process -FilePath $InstallerPath -ArgumentList @(
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_launcher=1",
        "Include_pip=1",
        "SimpleInstall=1"
    ) -Wait -PassThru
    Remove-Item $InstallerPath -Force -ErrorAction SilentlyContinue
    if ($Process.ExitCode -ne 0) {
        throw "The Python installer failed with exit code $($Process.ExitCode)."
    }
}

function Ensure-InnoSetup {
    $Candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
    )
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path $Candidate)) { return $Candidate }
    }
    Write-Step "Installing Inno Setup"
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) { throw "Inno Setup is missing and winget is unavailable." }
    & $Winget.Source install --exact --id JRSoftware.InnoSetup `
        --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "winget could not install Inno Setup." }
    foreach ($Candidate in $Candidates) {
        if ($Candidate -and (Test-Path $Candidate)) { return $Candidate }
    }
    $UserCandidate = Join-Path $env:LocalAppData "Programs\Inno Setup 6\ISCC.exe"
    if (Test-Path $UserCandidate) { return $UserCandidate }
    throw "Inno Setup was installed but ISCC.exe could not be located."
}

try {
    if ($env:OS -ne "Windows_NT") {
        throw "This build script must be run on Windows."
    }

    Set-Location $PSScriptRoot
    foreach ($RequiredFile in @("app.py", "Swarif.spec", "SwarifSetup.iss", "requirements-build.txt", ".env.example")) {
        if (-not (Test-Path (Join-Path $PSScriptRoot $RequiredFile))) {
            throw "Required project file is missing: $RequiredFile"
        }
    }

    $Python = Find-Python312
    if (-not $Python) {
        Install-Python312
        $Python = Find-Python312
    }
    if (-not $Python) {
        throw "Python 3.12 was installed but could not be located. Restart Windows and run this script again."
    }
    Write-Host "Using Python: $Python" -ForegroundColor Green

    $BuildEnvironment = Join-Path $PSScriptRoot ".build-venv"
    $BuildPython = Join-Path $BuildEnvironment "Scripts\python.exe"
    if (-not (Test-Path $BuildPython)) {
        Write-Step "Creating isolated build environment"
        & $Python -m venv $BuildEnvironment
        if ($LASTEXITCODE -ne 0) {
            throw "Could not create the build environment."
        }
    }

    Write-Step "Installing build requirements"
    & $BuildPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw "Could not update pip." }
    & $BuildPython -m pip install --requirement (Join-Path $PSScriptRoot "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) { throw "Could not install project requirements." }

    Write-Step "Compiling Swarif.exe"
    $BuildWorkPath = Join-Path $env:TEMP ("swarif-pyinstaller-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $BuildWorkPath -Force | Out-Null
    Write-Host "Using temporary build directory: $BuildWorkPath"
    & $BuildPython -m PyInstaller --clean --noconfirm `
        --workpath $BuildWorkPath `
        (Join-Path $PSScriptRoot "Swarif.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build Swarif." }

    $Output = Join-Path $PSScriptRoot "dist\Swarif.exe"
    if (-not (Test-Path $Output)) {
        throw "The build finished without producing dist\Swarif.exe."
    }

    $InnoCompiler = Ensure-InnoSetup
    Write-Step "Packaging SwarifSetup.exe"
    & $InnoCompiler (Join-Path $PSScriptRoot "SwarifSetup.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed to package SwarifSetup.exe." }
    $SetupOutput = Join-Path $PSScriptRoot "dist\SwarifSetup.exe"
    if (-not (Test-Path $SetupOutput)) { throw "The installer was not created." }

    Write-Host "`nBuild completed successfully:" -ForegroundColor Green
    Write-Host $SetupOutput -ForegroundColor White
    Write-Host "This installer contains Swarif. A local LLM is optional and is not bundled."
}
catch {
    Write-Host "`nBuild failed: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}
finally {
    if ($BuildWorkPath -and (Test-Path $BuildWorkPath)) {
        Remove-Item $BuildWorkPath -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not $NoPause) { Read-Host "Press Enter to close" }
