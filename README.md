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
- Fetches client-safe configuration from `api.swarif.com` at runtime. No
  provider or database secrets are bundled.

The finished files are:

```text
dist\Swarif.exe
dist\SwarifSetup.exe
```

You can distribute `SwarifSetup.exe` to Windows users.

If Windows Package Manager cannot install Inno Setup, `dist\Swarif.exe` is
still a successful application build. Install Inno Setup manually from its
official website and run `build-windows.ps1` again to create the installer.

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

The shared `Swarif.spec` build contains no `.env` file. Client-safe
configuration is fetched from `api.swarif.com` and cached in the user's Swarif
application-data directory.

Open the compiled application with:

```bash
open dist/Swarif.app
```

PyInstaller does not cross-compile. Build the Windows application on Windows
and the macOS application on macOS. Sign and notarize release builds before
distributing them publicly.
