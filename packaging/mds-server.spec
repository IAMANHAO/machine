# PyInstaller 规格 —— 把服务端 + 引擎 + 前端产物打成一个可执行文件。
#
#   cd <仓库根> && .venv/Scripts/pyinstaller.exe packaging/mds-server.spec --noconfirm
#
# 产物：dist/mds-server/mds-server.exe（onedir）
#
# 用 onedir 而不是 onefile：onefile 每次启动都要把几十 MB 解包到临时目录，
# 冷启动要多花好几秒；而 Tauri 的 sidecar 本来就随安装包分发，没必要压成一个文件。
#
# 随包分发的数据：
#   engine/  —— workflows + knowledge + references（只读；用户数据另放 %LOCALAPPDATA%）
#   web/     —— 前端构建产物
#
# 注意：knowledge/ 随包只读，所以打包版的知识库编辑功能会被 config.knowledge_writable()
# 关掉。要在打包版上改数据，得用 MDS_SKILL_ROOT 指一份可写的副本。

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
SKILL = ROOT / "engine"
WEB_DIST = ROOT / "web" / "dist"

if not (WEB_DIST / "index.html").exists():
    raise SystemExit(
        "前端还没构建。先跑：cd web && npm run build\n"
        f"（找不到 {WEB_DIST / 'index.html'}）")

datas = [
    # 引擎数据：规格、数据表、参考规范
    (str(SKILL / "workflows"), "engine/workflows"),
    (str(SKILL / "knowledge"), "engine/knowledge"),
    (str(SKILL / "references"), "engine/references"),
    (str(SKILL / "SKILL.md"), "engine"),
    # 采购关键词模板归 knowledge/procure/，已随 knowledge 一起打进去
    # 前端
    (str(WEB_DIST), "web"),
]

# reportlab 的 CID 字体资源（中文 PDF 全靠它，漏了导出就是空白）
datas += collect_data_files("reportlab")

hiddenimports = [
    # 引擎包住在 skill 目录里，PyInstaller 的静态分析扫不到
    "mds", "mds.runner", "mds.spec", "mds.expr", "mds.tables", "mds.rounding",
    "mds.knowledge", "mds.units", "mds.procure", "mds.errors", "mds.audit",
    "mds.editor",
    # 采购关键词拼装仍复用 skill 的 scripts/procure_link.py（那套 field_prefix
    # 规则是踩过坑才调对的，不重写）；它按运行时 import 加载，静态分析扫不到
    "procure_link", "_yaml", "yaml_mini",
    # uvicorn 的实现类都是按字符串加载的
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    # keyring 后端同样是运行时发现的；漏了就存不了凭据
    "keyring.backends.Windows", "keyring.backends.SecretService",
    "keyring.backends.macOS", "keyring.backends.chainer", "keyring.backends.fail",
]
hiddenimports += collect_submodules("ruamel.yaml")

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT), str(SKILL), str(SKILL / "scripts")],  # 要能 import 到 mds 与 procure_link
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 只排确定用不到的。注意 PIL 不能排——reportlab 的 lib/utils.py 硬 import 它，
        # 排掉的话 PDF 导出会在打包版上直接 ModuleNotFoundError（源码态试不出来）。
        "tkinter", "matplotlib", "scipy", "pandas",
        "pytest", "IPython", "notebook",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mds-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,          # sidecar 需要 stdout 回传 MDS_READY 那一行
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="mds-server",
)
