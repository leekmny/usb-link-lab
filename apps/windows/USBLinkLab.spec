# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path, PurePosixPath

app_dir = Path(SPECPATH)
analysis = Analysis(
    [str(app_dir / "launch.py")],
    pathex=[str(app_dir)],
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick"],
    optimize=0,
    module_collection_mode={"PySide6": "py", "shiboken6": "py"},
)

# Qt Widgets uses Windows platform, widget styles, and local image plugins.
# Windows 11 provides the UCRT. Microsoft distributes the separate VC runtime.
allowed_plugins = {"platforms", "styles", "imageformats", "iconengines"}


def include(entry):
    dest = PurePosixPath(entry[0].replace("\\", "/"))
    name = dest.name.lower()
    if (name.startswith(("api-ms-win-", "msvcp140", "vcruntime140"))
            or name in {"ucrtbase.dll", "opengl32sw.dll"}):
        return False
    parts = dest.parts
    return "plugins" not in parts or parts[parts.index("plugins") + 1] in allowed_plugins


analysis.binaries = [entry for entry in analysis.binaries if include(entry)]
analysis.datas = [entry for entry in analysis.datas if include(entry)]

archive = PYZ(analysis.pure)
program = EXE(
    archive, analysis.scripts, [], exclude_binaries=True, name="USBLinkLab",
    console=False, debug=False, upx=False,
)
bundle = COLLECT(
    program, analysis.binaries, analysis.datas, name="USBLinkLab", upx=False,
)
