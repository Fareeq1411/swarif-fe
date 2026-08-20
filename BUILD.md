# Building standalone Swarif releases

End users should receive a packaged build, not the Python source directory.
The packaged build includes Python and every package in `requirements.txt`, so
Python, pip, PyQt, and the OpenAI SDK do not need to be installed on the user's
laptop.

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

The script installs Python 3.12, Ollama, and Inno Setup when necessary. It
downloads/exports the configured `OLLAMA_MODEL`, builds the application, and
produces `dist\SwarifSetup.exe`. The setup executable includes `Swarif.exe`, a
portable GGUF model, and its Modelfile. On first startup Swarif imports that
model into Ollama and removes the temporary installer copy after successful
registration.

Outputs are written under `dist/`. Sign and notarize the macOS application and
code-sign the Windows executable before distribution. The application startup
screen handles Ollama installation, service startup, model download, and model
loading. Ollama installation and model downloads require internet access and
sufficient disk space; macOS may display its normal administrator prompt.
