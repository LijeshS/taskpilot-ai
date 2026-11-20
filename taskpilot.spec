# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for TaskPilot AI tray application
# Build with:  pyinstaller taskpilot.spec

import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

HERE = Path(SPECPATH)   # project root

# Collect all frontend dist files
frontend_dist = HERE / "frontend" / "dist"
frontend_datas = []
for f in frontend_dist.rglob("*"):
    if f.is_file():
        rel = f.relative_to(HERE)
        frontend_datas.append((str(f), str(rel.parent)))

# Backend Python files
backend_datas = [
    (str(HERE / "backend" / "main.py"),        "backend"),
    (str(HERE / "backend" / "requirements.txt"), "backend"),
]

# Tray icon
icon_datas = []
icon_file = HERE / "tray" / "icon.ico"
if icon_file.exists():
    icon_datas = [(str(icon_file), "tray")]

a = Analysis(
    [str(HERE / "tray" / "tray_app.py")],
    pathex=[str(HERE)],
    binaries=[],
    datas=frontend_datas + backend_datas + icon_datas,
    hiddenimports=[
        # FastAPI / uvicorn stack
        "uvicorn",
        "uvicorn.main",
        "uvicorn.config",
        "uvicorn.lifespan.on",
        "uvicorn.protocols.http.h11_impl",
        "fastapi",
        "fastapi.middleware.cors",
        "starlette",
        "starlette.staticfiles",
        "anyio",
        "anyio._backends._asyncio",
        # Data / ML
        "psutil",
        "sklearn",
        "sklearn.ensemble",
        "sklearn.neighbors",
        "numpy",
        # Email / keyring
        "keyring",
        "keyring.backends.Windows",
        "smtplib",
        "email",
        "email.mime.multipart",
        "email.mime.text",
        # Tray
        "pystray",
        "PIL",
        "PIL.Image",
        # DB
        "sqlite3",
        # Misc
        "dotenv",
    ]
    # backend/main.py is bundled as data and imported at runtime, so PyInstaller
    # cannot see its imports; collect the server stack explicitly.
    + collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + collect_submodules("fastapi")
    + collect_submodules("starlette")
    + collect_submodules("pydantic"),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "notebook"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TaskPilotAI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no console window
    icon=str(HERE / "tray" / "icon.ico") if icon_file.exists() else None,
    uac_admin=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TaskPilotAI",
)
