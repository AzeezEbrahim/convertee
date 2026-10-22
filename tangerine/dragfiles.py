"""Read the file list of an OLE drag session in progress on Windows.

During DoDragDrop the drag source publishes its data object on the OLE
clipboard, so the same trick Finder gives the macOS app (reading the file
URLs before the pointer enters the drop target) works here with CF_HDROP.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, byref, c_void_p, create_unicode_buffer, wintypes

IS_WINDOWS = sys.platform == "win32"

CF_HDROP = 15
DVASPECT_CONTENT = 1
TYMED_HGLOBAL = 1
DRAG_LOOP_FORMAT = "InShellDragLoop"

if IS_WINDOWS:
    ole32 = ctypes.windll.ole32
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32

    shell32.DragQueryFileW.argtypes = [
        wintypes.HANDLE,
        wintypes.UINT,
        ctypes.c_wchar_p,
        wintypes.UINT,
    ]
    shell32.DragQueryFileW.restype = wintypes.UINT

    user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT

    _IN_SHELL_DRAG_LOOP = user32.RegisterClipboardFormatW(DRAG_LOOP_FORMAT)
else:
    # On X11 the payload of the drag in flight is the XdndSelection.
    ole32 = shell32 = user32 = None
    _IN_SHELL_DRAG_LOOP = 0


class FORMATETC(ctypes.Structure):
    _fields_ = [
        ("cfFormat", wintypes.WORD),
        ("ptd", c_void_p),
        ("dwAspect", wintypes.DWORD),
        ("lindex", ctypes.c_long),
        ("tymed", wintypes.DWORD),
    ]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [
        ("tymed", wintypes.DWORD),
        ("hGlobal", wintypes.HANDLE),
        ("pUnkForRelease", c_void_p),
    ]


def _vfn(ptr, index, restype, *argtypes):
    vtable = ctypes.cast(ptr, POINTER(c_void_p))[0]
    proto = ctypes.WINFUNCTYPE(restype, c_void_p, *argtypes)
    return proto(ctypes.cast(vtable, POINTER(c_void_p))[index])


def _release(ptr) -> None:
    try:
        _vfn(ptr, 2, ctypes.c_ulong)(ptr)
    except Exception:
        pass


def _has_format(data_object, cf_format: int) -> bool:
    query = _vfn(data_object, 5, ctypes.c_long, POINTER(FORMATETC))
    fmt = FORMATETC(cf_format, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
    return query(data_object, byref(fmt)) == 0


def current_drag_files(require_drag_loop: bool = True) -> list[str] | None:
    """Return the files published on the OLE clipboard.

    ``require_drag_loop`` keeps the strict check that an actual shell drag
    session is in progress (the InShellDragLoop clipboard format). The
    monitor relaxes it only when the foreground window is an allowlisted
    Explorer surface during a held drag, where a plain CF_HDROP read is a
    safe fallback (some Windows builds do not publish the loop format).
    """

    if not IS_WINDOWS:
        from . import x11

        return x11.session().drag_files()

    data_object = c_void_p()
    try:
        if ole32.OleGetClipboard(byref(data_object)) != 0 or not data_object.value:
            return None
        if require_drag_loop and not _has_format(
            data_object.value, _IN_SHELL_DRAG_LOOP
        ):
            return None
        get_data = _vfn(
            data_object.value,
            3,
            ctypes.c_long,
            POINTER(FORMATETC),
            POINTER(STGMEDIUM),
        )
        fmt = FORMATETC(CF_HDROP, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
        medium = STGMEDIUM()
        if get_data(data_object.value, byref(fmt), byref(medium)) != 0:
            return None
        try:
            if not medium.hGlobal:
                return None
            count = shell32.DragQueryFileW(medium.hGlobal, 0xFFFFFFFF, None, 0)
            files: list[str] = []
            for index in range(int(count)):
                length = shell32.DragQueryFileW(medium.hGlobal, index, None, 0)
                buffer = create_unicode_buffer(int(length) + 1)
                shell32.DragQueryFileW(
                    medium.hGlobal, index, buffer, int(length) + 1
                )
                if buffer.value:
                    files.append(buffer.value)
            return files or None
        finally:
            ole32.ReleaseStgMedium(byref(medium))
    except Exception:
        return None
    finally:
        if data_object.value:
            _release(data_object.value)
