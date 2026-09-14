# Build with: pyinstaller --clean --noconfirm Swarif.spec
import sys
import tempfile
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


datas = collect_data_files("qtawesome")
datas.append((".env.example", "."))
datas.append(("FRONT_AGENT_BEHAVIOR.md", "."))
datas.append(("static/Swarif_Logo.svg", "static"))
datas.append(("static/Swarif_Logo.ico", "static"))
datas.append(("static/Swarif_Logo_BG.svg", "static"))
datas.append(("static/Swarif_Logo_BG.png", "static"))
datas.append(("static/Swarif_Logo_BG.ico", "static"))
runtime_config_dir = Path(tempfile.mkdtemp(prefix="swarif-runtime-config-"))
runtime_env = runtime_config_dir / ".env"
env_source = Path(SPECPATH) / ".env.example"
env_bytes = env_source.read_bytes()
if env_bytes.startswith((b"\xff\xfe", b"\xfe\xff")):
    env_text = env_bytes.decode("utf-16")
else:
    env_text = env_bytes.decode("utf-8-sig")
runtime_env.write_text(env_text, encoding="utf-8", newline="\n")
datas.append((str(runtime_env), "."))

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if sys.platform == "win32":
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        exclude_binaries=False,
        name="Swarif",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        icon=str(Path(SPECPATH) / "static" / "Swarif_Logo_BG.ico"),
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="Swarif",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="Swarif",
    )

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Swarif.app",
        icon=None,
        bundle_identifier="com.swarif.desktop",
    )
