"""Windows taskbar identity for a window hosted by a generic executable (pythonw.exe).

The taskbar button's jump list shows the *process executable's* name and icon ("Python") unless
the window carries System.AppUserModel.RelaunchDisplayNameResource / RelaunchIconResource /
RelaunchCommand. Those are set through the window's property store (IPropertyStore), done here
with ctypes so no extra dependency is needed. No-op on other platforms and in the packaged app
(MirrorVoice.exe already carries name and icon).
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

APP_ID = "io.github.mirrorvoice"


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, s: str) -> "_GUID":
        import uuid

        u = uuid.UUID(s)
        g = cls()
        g.Data1, g.Data2, g.Data3 = u.time_low, u.time_mid, u.time_hi_version
        g.Data4[:] = u.bytes[8:]
        return g


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):  # only the VT_LPWSTR shape is needed
    _fields_ = [("vt", wintypes.USHORT), ("r1", wintypes.USHORT), ("r2", wintypes.USHORT), ("r3", wintypes.USHORT),
                ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


_FMTID = "9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"  # PKEY_AppUserModel_*
_PID_RELAUNCH_COMMAND, _PID_RELAUNCH_ICON, _PID_RELAUNCH_NAME, _PID_ID = 2, 3, 4, 5
_IID_IPROPERTYSTORE = "886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"
_VT_LPWSTR = 31

# IPropertyStore vtable: IUnknown(3) + GetCount, GetAt, GetValue, SetValue, Commit
_SetValue = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY), ctypes.POINTER(_PROPVARIANT))
_Commit = ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p)
_Release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def set_window_identity(hwnd: int, name: str, icon_path: str, command: str, app_id: str = APP_ID) -> bool:
    """Make the taskbar show `name` + `icon_path` (and relaunch with `command`) for this window."""
    if sys.platform != "win32":
        return False
    try:
        shell32 = ctypes.windll.shell32
        store = ctypes.c_void_p()
        iid = _GUID.of(_IID_IPROPERTYSTORE)
        hr = shell32.SHGetPropertyStoreForWindow(wintypes.HWND(hwnd), ctypes.byref(iid), ctypes.byref(store))
        if hr != 0 or not store:
            return False
        vtbl = ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0]
        funcs = ctypes.cast(vtbl, ctypes.POINTER(ctypes.c_void_p))
        set_value, commit, release = _SetValue(funcs[6]), _Commit(funcs[7]), _Release(funcs[2])
        keep = []  # strings must outlive SetValue
        ok = True
        for pid, value in ((_PID_ID, app_id), (_PID_RELAUNCH_NAME, name),
                           (_PID_RELAUNCH_ICON, icon_path), (_PID_RELAUNCH_COMMAND, command)):
            key = _PROPERTYKEY(_GUID.of(_FMTID), pid)
            pv = _PROPVARIANT()
            pv.vt = _VT_LPWSTR
            buf = ctypes.c_wchar_p(value)
            keep.append(buf)
            pv.pwszVal = buf
            ok = set_value(store, ctypes.byref(key), ctypes.byref(pv)) == 0 and ok
        ok = commit(store) == 0 and ok
        release(store)
        return ok
    except Exception:  # noqa: BLE001 — cosmetic; never break startup over it
        return False
