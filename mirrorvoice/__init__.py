"""镜声 MirrorVoice — local real-time speech translation in your own cloned voice."""

__version__ = "0.8.16"
APP_NAME = "镜声"
APP_ID = "MirrorVoice"

# Running a code patch (packaging/patchboot.py): let Python cache its compiled modules in the patch
# folder, which the frozen app does not do by default — later starts skip compiling them again.
import os as _os
import sys as _sys

if _os.environ.get("MIRRORVOICE_PATCH"):
    _sys.dont_write_bytecode = False
