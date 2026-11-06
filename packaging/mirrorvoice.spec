# PyInstaller spec for 镜声 (macOS .app and Windows folder build).
#   macOS:   pyinstaller packaging/mirrorvoice.spec   -> dist/镜声.app
#   Windows: pyinstaller packaging/mirrorvoice.spec   -> dist/MirrorVoice/{MirrorVoice.exe,MirrorVoiceCLI.exe}
# Models are NOT bundled; the first-run wizard downloads them.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))
from mirrorvoice import __version__ as VERSION  # noqa: E402
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
VENDOR = ROOT / "packaging" / "vendor"

datas = [(str(ROOT / "mirrorvoice" / "tts_server.py"), "mirrorvoice"),
         (str(ROOT / "packaging" / "assets" / "icon.png"), "assets"),  # window / tray icon at runtime
         (str(ROOT / "packaging" / "assets" / "chevron-down.svg"), "assets"),  # combo box arrow (stylesheet)
         (str(ROOT / "packaging" / "assets" / "menubar-template.png"), "assets"),  # macOS menu-bar icon
         (str(ROOT / "packaging" / "assets" / "check-white.svg"), "assets"),  # menu check mark (stylesheet)
         (str(ROOT / "LICENSE"), "."), (str(ROOT / "THIRD_PARTY_NOTICES.md"), ".")]
binaries = []
hiddenimports = []


def take(pkg):
    d, b, h = collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hiddenimports.extend(h)


for pkg in ("sherpa_onnx", "sounddevice", "_sounddevice_data", "num2words", "platformdirs", "certifi", "truststore"):
    take(pkg)
# Qt's Chinese strings for standard dialog buttons (PySide6/translations is not collected by default)
from PySide6.QtCore import QLibraryInfo  # noqa: E402
_tr = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)) / "qtbase_zh_CN.qm"
if _tr.exists():
    datas.append((str(_tr), "PySide6/translations"))
hiddenimports += collect_submodules("pynput")

if IS_MAC:
    for pkg in ("mlx", "mlx_lm", "mlx_audio", "mlx_metal"):
        take(pkg)
    # tokenizer + config classes that transformers imports by name at runtime
    hiddenimports += collect_submodules("transformers.models.qwen2")
    hiddenimports += collect_submodules("transformers.models.qwen3")
    datas += collect_data_files("transformers", include_py_files=False)
    hiddenimports += ["AppKit", "Foundation", "HIServices", "objc", "PyObjCTools.AppHelper"]
    for f in ("BlackHole2ch-0.7.1.pkg", "BlackHole16ch-0.7.1.pkg", "multi_output"):
        p = VENDOR / f
        if p.exists():
            (binaries if f == "multi_output" else datas).append((str(p), "."))
if IS_WIN:
    take("soundcard")
    for sub in ("llama", "uv.exe"):
        p = VENDOR / sub
        if p.is_dir():
            datas.append((str(p), "llama"))
        elif p.exists():
            binaries.append((str(p), "."))

excludes = ["torch", "torchaudio", "torchvision", "modelscope", "datasets", "pandas", "tensorflow", "jax", "gradio", "matplotlib",
            "IPython", "notebook", "pytest", "tkinter", "PySide6.QtWebEngineCore", "PySide6.Qt3DCore",
            "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia", "PySide6.QtPdf"]

a = Analysis([str(ROOT / "packaging" / "entry.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, excludes=excludes, noarchive=False)
pyz = PYZ(a.pure)

icon = str(ROOT / "packaging" / "assets" / ("icon.icns" if IS_MAC else "icon.ico"))
gui_exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="MirrorVoice", console=False, icon=icon,
              argv_emulation=False, target_arch=None, codesign_identity=None, entitlements_file=None)
exes = [gui_exe]
if IS_WIN:  # console twin for the command-line display mode (a windowed exe has no console)
    exes.append(EXE(pyz, a.scripts, [], exclude_binaries=True, name="MirrorVoiceCLI", console=True, icon=icon))

coll = COLLECT(*exes, a.binaries, a.datas, strip=False, upx=False, name="MirrorVoice")

if IS_MAC:
    app = BUNDLE(
        coll, name="镜声.app", icon=icon, bundle_identifier="io.github.mirrorvoice",
        info_plist={
            "CFBundleDisplayName": "镜声",
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "LSMinimumSystemVersion": "14.0",
            "NSMicrophoneUsageDescription": "镜声需要麦克风来识别你说的话并实时翻译。",
            "NSAppleEventsUsageDescription": "命令行模式需要打开“终端”窗口。",
            "NSHighResolutionCapable": True,
        },
    )
