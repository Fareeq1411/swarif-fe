param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

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

function Find-Ollama {
    $Command = Get-Command ollama.exe -ErrorAction SilentlyContinue
    if ($Command -and $Command.Source -notmatch "\\WindowsApps\\") {
        return $Command.Source
    }
    $Candidate = Join-Path $env:LocalAppData "Programs\Ollama\ollama.exe"
    if (Test-Path $Candidate) { return $Candidate }
    return $null
}

function Ensure-OllamaForBuild {
    $Ollama = Find-Ollama
    if ($Ollama) { return $Ollama }
    Write-Step "Installing Ollama for the offline model export"
    $Winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $Winget) {
        throw "Ollama is missing and winget is unavailable. Install Ollama, then run this script again."
    }
    & $Winget.Source install --exact --id Ollama.Ollama --scope user `
        --accept-package-agreements --accept-source-agreements --silent
    if ($LASTEXITCODE -ne 0) { throw "winget could not install Ollama." }
    $Ollama = Find-Ollama
    if (-not $Ollama) { throw "Ollama was installed but ollama.exe could not be located." }
    return $Ollama
}

function Export-OfflineModel([string]$Ollama, [string]$Model) {
    Write-Step "Preparing offline Ollama model: $Model"
    & $Ollama list *> $null
    if ($LASTEXITCODE -ne 0) {
        Start-Process -FilePath $Ollama -ArgumentList "serve" -WindowStyle Hidden | Out-Null
        $Ready = $false
        foreach ($Attempt in 1..30) {
            Start-Sleep -Seconds 1
            & $Ollama list *> $null
            if ($LASTEXITCODE -eq 0) { $Ready = $true; break }
        }
        if (-not $Ready) { throw "Ollama did not start on the build laptop." }
    }
    & $Ollama show $Model *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The model is not installed on this build laptop; downloading it now."
        & $Ollama pull $Model
        if ($LASTEXITCODE -ne 0) { throw "Ollama could not download $Model." }
    }

    $ModelfileText = (& $Ollama show --modelfile $Model | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $ModelfileText) {
        throw "Could not export the Modelfile for $Model."
    }
    $FromMatch = [regex]::Match($ModelfileText, '(?m)^FROM\s+(.+?)\s*$')
    if (-not $FromMatch.Success) { throw "The exported Modelfile has no FROM model blob." }
    $BlobPath = $FromMatch.Groups[1].Value.Trim().Trim('"')
    if (-not (Test-Path $BlobPath -PathType Leaf)) {
        throw "The Ollama model blob could not be found: $BlobPath"
    }

    $Bundle = Join-Path $PSScriptRoot "build-assets\model_bundle"
    New-Item -ItemType Directory -Path $Bundle -Force | Out-Null
    Copy-Item $BlobPath (Join-Path $Bundle "model.gguf") -Force
    $PortableModelfile = [regex]::Replace(
        $ModelfileText,
        '(?m)^FROM\s+.+?\s*$',
        'FROM ./model.gguf',
        1
    )
    [IO.File]::WriteAllText(
        (Join-Path $Bundle "Modelfile"),
        $PortableModelfile + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    [IO.File]::WriteAllText(
        (Join-Path $Bundle "model-name.txt"),
        $Model + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
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
    & $BuildPython -m PyInstaller --clean --noconfirm (Join-Path $PSScriptRoot "Swarif.spec")
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed to build Swarif." }

    $Output = Join-Path $PSScriptRoot "dist\Swarif.exe"
    if (-not (Test-Path $Output)) {
        throw "The build finished without producing dist\Swarif.exe."
    }

    $Model = "qwen3:8b"
    $EnvFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path $EnvFile) {
        $ModelLine = Get-Content $EnvFile | Where-Object { $_ -match '^\s*OLLAMA_MODEL\s*=' } | Select-Object -First 1
        if ($ModelLine) { $Model = ($ModelLine -split '=', 2)[1].Trim() }
    }
    if (-not $Model) { throw "OLLAMA_MODEL is empty." }
    $Ollama = Ensure-OllamaForBuild
    Export-OfflineModel $Ollama $Model

    $InnoCompiler = Ensure-InnoSetup
    Write-Step "Packaging SwarifSetup.exe with the offline model"
    & $InnoCompiler (Join-Path $PSScriptRoot "SwarifSetup.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed to package SwarifSetup.exe." }
    $SetupOutput = Join-Path $PSScriptRoot "dist\SwarifSetup.exe"
    if (-not (Test-Path $SetupOutput)) { throw "The installer was not created." }

    Write-Host "`nBuild completed successfully:" -ForegroundColor Green
    Write-Host $SetupOutput -ForegroundColor White
    Write-Host "This installer contains Swarif and the configured offline Ollama model."
}
catch {
    Write-Host "`nBuild failed: $($_.Exception.Message)" -ForegroundColor Red
    if (-not $NoPause) { Read-Host "Press Enter to close" }
    exit 1
}

if (-not $NoPause) { Read-Host "Press Enter to close" }
