"""Ollama model bootstrap service and startup UI."""

import json
import os
import platform
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PyQt5.QtCore import Qt, pyqtSignal, QObject
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"


class OllamaBootstrap(QObject):
    """Ensure the configured Ollama model exists and is loaded."""

    status_changed = pyqtSignal(str)
    progress_changed = pyqtSignal(int)
    finished = pyqtSignal(bool, str)

    def __init__(self, base_url=None, model=None, parent=None):
        super().__init__(parent)
        self.base_url = (base_url or DEFAULT_OLLAMA_URL).rstrip("/")
        self.model = (model or DEFAULT_OLLAMA_MODEL).strip()
        self._thread = None
        self._server_process = None
        self._ollama_executable = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="swarif-ollama-bootstrap",
            daemon=True,
        )
        self._thread.start()

    def _run(self):
        try:
            if not self.model:
                raise RuntimeError("The local LLM model is not configured")
            self.status_changed.emit("Starting Ollama…")
            self.progress_changed.emit(-1)
            self._ensure_ollama_running()
            self.status_changed.emit("Checking Ollama…")
            bundled_model = self._find_bundled_model()
            if not self._model_exists():
                if bundled_model is not None:
                    self._import_bundled_model(bundled_model)
                else:
                    self._pull_model()
            else:
                self.progress_changed.emit(85)
                self.status_changed.emit(f"{self.model} is installed")
            self._load_model()
            self.progress_changed.emit(100)
            self.status_changed.emit("Swarif is ready")
            self.finished.emit(True, "")
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            self.finished.emit(False, str(error))

    def _ensure_ollama_running(self):
        try:
            self._probe_ollama()
            return
        except (URLError, OSError):
            pass

        executable = self._find_ollama_executable()
        if executable is None:
            self._install_ollama()
            executable = self._find_ollama_executable()
            if executable is None:
                raise RuntimeError("Ollama installation finished, but its executable was not found")
        self._ollama_executable = executable

        creation_flags = 0
        if platform.system() == "Windows":
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self._server_process = subprocess.Popen(
                [executable, "serve"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
        except OSError as error:
            raise RuntimeError(f"Could not start Ollama: {error}") from error

        self.status_changed.emit("Waiting for Ollama to start…")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._server_process.poll() is not None:
                raise RuntimeError("Ollama stopped before its API became ready")
            try:
                self._probe_ollama()
                return
            except (URLError, OSError):
                time.sleep(0.5)
        self.stop_owned_server()
        raise RuntimeError("Ollama did not become ready within 30 seconds")

    def _find_bundled_model(self):
        """Find an offline GGUF/Modelfile bundle installed with Swarif."""
        candidates = []
        configured = os.getenv("SWARIF_BUNDLED_MODEL_DIR")
        if configured:
            candidates.append(Path(configured))
        if platform.system() == "Windows" and os.getenv("LOCALAPPDATA"):
            candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Swarif/model_bundle")
        elif platform.system() == "Darwin":
            candidates.append(Path.home() / "Library/Application Support/Swarif/model_bundle")
        candidates.append(Path(__file__).resolve().parent / "model_bundle")

        for directory in candidates:
            if (directory / "model.gguf").is_file() and (directory / "Modelfile").is_file():
                name_file = directory / "model-name.txt"
                if name_file.is_file():
                    bundled_name = name_file.read_text(encoding="utf-8").strip()
                    if bundled_name:
                        self.model = bundled_name
                return directory
        return None

    def _import_bundled_model(self, directory):
        """Register the packaged GGUF through Ollama's supported create command."""
        executable = self._ollama_executable or self._find_ollama_executable()
        if executable is None:
            raise RuntimeError("Ollama is running, but its command-line executable was not found")
        self.status_changed.emit(f"Installing offline model {self.model}…")
        self.progress_changed.emit(-1)
        try:
            completed = subprocess.run(
                [executable, "create", self.model, "-f", str(directory / "Modelfile")],
                cwd=str(directory),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1800,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"Could not import the bundled Ollama model: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "import failed"
            raise RuntimeError(f"Could not import the bundled Ollama model: {detail}")
        if not self._model_exists():
            raise RuntimeError("Ollama did not register the bundled model")
        try:
            shutil.rmtree(directory)
        except OSError:
            pass

    def _install_ollama(self):
        """Run Ollama's official installer for the current desktop platform."""
        system = platform.system()
        self.status_changed.emit("Installing Ollama…")
        self.progress_changed.emit(-1)
        if system == "Windows":
            command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                "irm https://ollama.com/install.ps1 | iex",
            ]
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        elif system == "Darwin":
            if int(platform.mac_ver()[0].split(".")[0] or 0) < 14:
                raise RuntimeError("Ollama requires macOS 14 Sonoma or newer")
            installer = "/usr/bin/curl -fsSL https://ollama.com/install.sh | /bin/sh"
            command = [
                "/usr/bin/osascript",
                "-e",
                f'do shell script "{installer}" with administrator privileges',
            ]
            creation_flags = 0
        elif system == "Linux":
            command = [
                "/bin/sh",
                "-c",
                "curl -fsSL https://ollama.com/install.sh | sh",
            ]
            creation_flags = 0
        else:
            raise RuntimeError(f"Automatic Ollama installation is not supported on {system}")

        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
                creationflags=creation_flags,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(f"Could not install Ollama: {error}") from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "installer failed"
            raise RuntimeError(f"Could not install Ollama: {detail}")

    def _probe_ollama(self):
        request = Request(
            f"{self.base_url}/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        with urlopen(request, timeout=2) as response:
            response.read(1)

    @staticmethod
    def _find_ollama_executable():
        executable = shutil.which("ollama")
        if executable:
            return executable
        candidates = []
        system = platform.system()
        if system == "Darwin":
            candidates.extend(
                [
                    "/Applications/Ollama.app/Contents/Resources/ollama",
                    "/usr/local/bin/ollama",
                    "/opt/homebrew/bin/ollama",
                ]
            )
        elif system == "Windows":
            local_app_data = os.getenv("LOCALAPPDATA")
            if local_app_data:
                candidates.append(str(Path(local_app_data) / "Programs/Ollama/ollama.exe"))
        return next((candidate for candidate in candidates if Path(candidate).is_file()), None)

    def stop_owned_server(self):
        """Stop Ollama only when this Swarif process launched it."""
        process = self._server_process
        self._server_process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def _model_exists(self):
        request = Request(
            f"{self.base_url}/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        result = self._open_json(request, timeout=5)
        models = result.get("models") if isinstance(result, dict) else None
        if not isinstance(models, list):
            raise RuntimeError("Ollama returned an invalid model list")
        installed = {
            value
            for item in models
            if isinstance(item, dict)
            for value in (item.get("name"), item.get("model"))
            if isinstance(value, str)
        }
        return self.model in installed

    def _pull_model(self):
        self.status_changed.emit(f"Downloading {self.model}…")
        self.progress_changed.emit(0)
        request = Request(
            f"{self.base_url}/api/pull",
            data=json.dumps({"name": self.model, "stream": True}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=30) as response:
                for raw_line in response:
                    if not raw_line.strip():
                        continue
                    event = json.loads(raw_line.decode("utf-8"))
                    error = event.get("error")
                    if error:
                        raise RuntimeError(f"Ollama could not download the model: {error}")
                    status = event.get("status")
                    if isinstance(status, str) and status:
                        self.status_changed.emit(status.capitalize())
                    completed = event.get("completed")
                    total = event.get("total")
                    if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and total > 0:
                        self.progress_changed.emit(min(84, int(completed / total * 84)))
        except HTTPError as error:
            raise RuntimeError(self._http_error("Ollama model download failed", error)) from error
        except URLError as error:
            raise RuntimeError(f"Could not connect to Ollama at {self.base_url}: {error.reason}") from error

    def _load_model(self):
        self.status_changed.emit(f"Loading {self.model}…")
        self.progress_changed.emit(90)
        request = Request(
            f"{self.base_url}/api/generate",
            data=json.dumps(
                {"model": self.model, "prompt": "", "stream": False, "keep_alive": "10m"}
            ).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        result = self._open_json(request, timeout=300)
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError(f"Ollama could not load {self.model}")

    def _open_json(self, request, timeout):
        try:
            with urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(self._http_error("Ollama request failed", error)) from error
        except URLError as error:
            raise RuntimeError(f"Could not connect to Ollama at {self.base_url}: {error.reason}") from error
        except json.JSONDecodeError as error:
            raise RuntimeError("Ollama returned invalid JSON") from error

    @staticmethod
    def _http_error(prefix, error):
        try:
            body = json.loads(error.read().decode("utf-8"))
            detail = body.get("error") or body.get("message") or str(error)
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            detail = str(error)
        return f"{prefix}: {detail}"


class BootstrapWindow(QWidget):
    """Small blocking startup window shown until Ollama is ready."""

    ready = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._completed = False
        self.setWindowTitle("Starting Swarif")
        self.setFixedSize(430, 220)
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        card = QFrame()
        card.setObjectName("bootstrapCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)
        title = QLabel("Starting Swarif")
        title.setObjectName("bootstrapTitle")
        self.status = QLabel("Preparing Ollama…")
        self.status.setObjectName("bootstrapStatus")
        self.status.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setObjectName("bootstrapProgress")
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(True)
        self.error = QLabel("")
        self.error.setObjectName("bootstrapError")
        self.error.setWordWrap(True)
        self.error.hide()
        buttons = QHBoxLayout()
        self.retry = QPushButton("Retry")
        self.retry.setObjectName("bootstrapButton")
        self.retry.hide()
        self.quit = QPushButton("Quit")
        self.quit.setObjectName("bootstrapQuit")
        self.quit.hide()
        buttons.addStretch()
        buttons.addWidget(self.retry)
        buttons.addWidget(self.quit)
        layout.addWidget(title)
        layout.addWidget(self.status)
        layout.addWidget(self.progress)
        layout.addWidget(self.error)
        layout.addLayout(buttons)
        root.addWidget(card)

        self.service = OllamaBootstrap(parent=self)
        self.service.status_changed.connect(self.status.setText)
        self.service.progress_changed.connect(self.set_progress)
        self.service.finished.connect(self.bootstrap_finished)
        self.retry.clicked.connect(self.start)
        self.quit.clicked.connect(QApplication.instance().quit)
        self.setStyleSheet("""
            QWidget { background: #F7FAFE; color: #15233A; font-family: Arial; }
            #bootstrapCard { background: white; border: 1px solid #D9E5F2; border-radius: 18px; }
            #bootstrapTitle { font-size: 21px; font-weight: 700; }
            #bootstrapStatus { color: #74839A; font-size: 12px; }
            #bootstrapProgress { min-height: 20px; border: 1px solid #D8E3F0; border-radius: 9px; text-align: center; }
            #bootstrapProgress::chunk { background: #2478EB; border-radius: 8px; }
            #bootstrapError { color: #C3424D; font-size: 11px; }
            #bootstrapButton { background: #2478EB; color: white; border: none; border-radius: 8px; padding: 8px 18px; }
            #bootstrapQuit { background: #EDF1F5; color: #526176; border: none; border-radius: 8px; padding: 8px 18px; }
        """)

    def start(self):
        self.error.hide()
        self.retry.hide()
        self.quit.hide()
        self.progress.setRange(0, 0)
        self.status.setText("Preparing Ollama…")
        self.service.start()

    def set_progress(self, value):
        if value < 0:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(value)

    def bootstrap_finished(self, success, message):
        if success:
            self._completed = True
            self.ready.emit()
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText("Swarif could not start")
        self.error.setText(message)
        self.error.show()
        self.retry.show()
        self.quit.show()

    def closeEvent(self, event):
        if not self._completed:
            QApplication.instance().quit()
        super().closeEvent(event)
