# Building standalone Swarif releases

End users should receive a packaged build, not the Python source directory.
The packaged build includes Python and every package in `requirements.txt`, so
Python, pip, and PyQt do not need to be installed on the user's
laptop.

On both Windows and macOS, `Swarif.spec` packages no `.env` file. Client-safe
configuration is fetched from the Swarif API and cached in the user's
application-data directory.

Build on each target operating system; PyInstaller does not cross-compile.

```text
python -m venv .build-venv
.build-venv/bin/pip install -r requirements-build.txt
.build-venv/bin/pyinstaller --clean --noconfirm Swarif.spec
```

On Windows, use `.build-venv\Scripts\pip.exe` and
`.build-venv\Scripts\pyinstaller.exe` instead.

For a one-command Windows build, right-click `build-windows.ps1` and choose
**Run with PowerShell**, or run:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

The script installs Python 3.12 and Inno Setup when necessary, creates the
isolated build environment, installs all build dependencies, and produces
`dist\SwarifSetup.exe`. A local LLM is optional and is not bundled.

Outputs are written under `dist/`. Sign and notarize the macOS application and
code-sign the Windows executable before distribution.
