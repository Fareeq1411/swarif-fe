# Swarif Front Agent

Swarif Front Agent is a desktop application built with Python and PyQt5.
A local LLM is optional and is not required or bundled with the application.

## Compile on Windows

Open PowerShell in the project directory and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

The script performs the complete build automatically:

- Installs Python 3.12 when it is missing.
- Creates an isolated build environment.
- Installs the required Python packages.
- Compiles `Swarif.exe`.
- Installs Inno Setup when it is missing.
- Creates the Windows installer.
- Uses a temporary PyInstaller work directory outside the project, avoiding
  common OneDrive file-lock errors.

The finished files are:

```text
dist\Swarif.exe
dist\SwarifSetup.exe
```

You can distribute `SwarifSetup.exe` to Windows users.

## Compile on macOS

Install Python 3 first. With [Homebrew](https://brew.sh/), you can run:

```bash
brew install python@3.12
```

Then open Terminal in the project directory and run:

```bash
python3.12 -m venv .build-venv
.build-venv/bin/python -m pip install --upgrade pip
.build-venv/bin/python -m pip install -r requirements-build.txt
.build-venv/bin/python -m PyInstaller --clean --noconfirm Swarif.spec
```

The macOS application is created at:

```text
dist/Swarif.app
```

Open the compiled application with:

```bash
open dist/Swarif.app
```

PyInstaller does not cross-compile. Build the Windows application on Windows
and the macOS application on macOS. Sign and notarize release builds before
distributing them publicly.
