"""External-tool DLL isolation for the Windows PyInstaller runtime.

The bootloader DLL directory is process-wide. Keep its reset confined to the
short CreateProcess window and restore the exact previous value under one lock.
The dedicated ingestion worker can reset permanently because it runs no ML.
"""
from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
import threading

_DLL_LOCK = threading.RLock()


def _windows_dll_api():
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.GetDllDirectoryW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p]
    kernel32.GetDllDirectoryW.restype = ctypes.c_uint32
    kernel32.SetDllDirectoryW.argtypes = [ctypes.c_wchar_p]
    kernel32.SetDllDirectoryW.restype = ctypes.c_int
    return kernel32


def reset_worker_dll_directory() -> None:
    if os.name == 'nt' and getattr(sys, 'frozen', False):
        if not _windows_dll_api().SetDllDirectoryW(None):
            raise OSError(ctypes.get_last_error(), 'Could not sanitize the worker DLL search directory.')


@contextlib.contextmanager
def external_dll_search():
    if os.name != 'nt' or not getattr(sys, 'frozen', False):
        yield
        return
    with _DLL_LOCK:
        api = _windows_dll_api()
        required = api.GetDllDirectoryW(0, None)
        previous = ctypes.create_unicode_buffer(required + 1)
        if required:
            copied = api.GetDllDirectoryW(len(previous), previous)
            if copied > required:
                raise OSError('The DLL search directory changed during capture.')
        if not api.SetDllDirectoryW(None):
            raise OSError(ctypes.get_last_error(), 'Could not isolate the external tool DLL directory.')
        try:
            yield
        finally:
            if not api.SetDllDirectoryW(previous.value if required else None):
                raise OSError(ctypes.get_last_error(), 'Could not restore the packaged DLL directory.')


def external_popen(*args, **kwargs):
    with external_dll_search():
        return subprocess.Popen(*args, **kwargs)
