"""Small native Windows primitives for the repository's desktop launcher."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import os
import time


class LauncherWindowsError(RuntimeError):
    pass


APP_USER_MODEL_ID = "ExhentaiSelfRec.Launcher"


def set_taskbar_identity() -> bool:
    """Separate the launcher from Python's taskbar group before creating Tk."""
    if os.name != "nt":
        return False
    try:
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        method = shell.SetCurrentProcessExplicitAppUserModelID
        method.argtypes, method.restype = [W.LPCWSTR], W.LONG
        return method(APP_USER_MODEL_ID) == 0
    except (OSError, AttributeError):
        return False


class BasicLimits(ctypes.Structure):
    _fields_ = [("user_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                ("flags", W.DWORD), ("minimum", ctypes.c_size_t), ("maximum", ctypes.c_size_t),
                ("process_limit", W.DWORD), ("affinity", ctypes.c_size_t),
                ("priority", W.DWORD), ("scheduling", W.DWORD)]


class IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("basic", BasicLimits), ("io", IoCounters),
                ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]


class Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
        (name, W.DWORD) for name in ("faults", "total", "active", "terminated")]


def kernel():
    if os.name != "nt":
        raise LauncherWindowsError("windows-required")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([ctypes.c_void_p, W.LPCWSTR], W.HANDLE),
        "SetInformationJobObject": ([W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD], W.BOOL),
        "QueryInformationJobObject": ([W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.c_void_p], W.BOOL),
        "AssignProcessToJobObject": ([W.HANDLE, W.HANDLE], W.BOOL),
        "TerminateJobObject": ([W.HANDLE, W.UINT], W.BOOL),
        "OpenProcess": ([W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
        "CloseHandle": ([W.HANDLE], W.BOOL),
        "CreateMutexW": ([ctypes.c_void_p, W.BOOL, W.LPCWSTR], W.HANDLE),
        "OpenMutexW": ([W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE),
        "ReleaseMutex": ([W.HANDLE], W.BOOL),
        "WaitForSingleObject": ([W.HANDLE, W.DWORD], W.DWORD),
        "CreateEventW": ([ctypes.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR], W.HANDLE),
        "OpenEventW": ([W.DWORD, W.BOOL, W.LPCWSTR], W.HANDLE),
        "SetEvent": ([W.HANDLE], W.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        method = getattr(api, name)
        method.argtypes, method.restype = arguments, result
    return api


class WindowsJob:
    def __init__(self):
        self.api = kernel()
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise LauncherWindowsError("containment-unavailable")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise LauncherWindowsError("containment-unavailable")

    def assign(self, pid: int):
        process = self.api.OpenProcess(0x0100 | 0x0001, False, pid)
        if not process:
            raise LauncherWindowsError("containment-unavailable")
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise LauncherWindowsError("containment-unavailable")
        finally:
            self.api.CloseHandle(process)

    def active(self) -> int:
        info = Accounting()
        if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
            raise LauncherWindowsError("containment-readback-failed")
        return info.active

    def stop(self):
        if not self.api.TerminateJobObject(self.handle, 0):
            raise LauncherWindowsError("stop-failed")
        deadline = time.monotonic() + 10
        while self.active():
            if time.monotonic() >= deadline:
                raise LauncherWindowsError("stop-timeout")
            time.sleep(0.025)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ProfileMutex:
    def __init__(self, name: str):
        self.api = kernel()
        self.handle = self.api.CreateMutexW(None, False, name)
        if not self.handle:
            raise LauncherWindowsError("mutex-unavailable")
        result = self.api.WaitForSingleObject(self.handle, 0)
        self.owned = result in (0, 0x80)
        if not self.owned and result != 0x102:
            self.close()
            raise LauncherWindowsError("mutex-unavailable")

    def close(self):
        if self.handle:
            if getattr(self, "owned", False):
                self.api.ReleaseMutex(self.handle)
            self.api.CloseHandle(self.handle)
            self.handle = None


def mutex_active(name: str) -> bool:
    api = kernel()
    handle = api.OpenMutexW(0x00100000 | 0x0001, False, name)
    if not handle:
        if ctypes.get_last_error() == 2:
            return False
        raise LauncherWindowsError("mutex-readback-failed")
    try:
        result = api.WaitForSingleObject(handle, 0)
        if result in (0, 0x80):
            api.ReleaseMutex(handle)
            return False
        if result == 0x102:
            return True
        raise LauncherWindowsError("mutex-readback-failed")
    finally:
        api.CloseHandle(handle)


class StopEvent:
    def __init__(self, name: str):
        self.api = kernel()
        self.handle = self.api.CreateEventW(None, True, False, name)
        if not self.handle:
            raise LauncherWindowsError("stop-event-unavailable")

    def wait(self, milliseconds=250) -> bool:
        result = self.api.WaitForSingleObject(self.handle, milliseconds)
        if result not in (0, 0x102):
            raise LauncherWindowsError("stop-event-failed")
        return result == 0

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def signal_stop(name: str):
    api = kernel()
    handle = api.OpenEventW(0x0002, False, name)
    if not handle:
        raise LauncherWindowsError("stop-event-missing")
    try:
        if not api.SetEvent(handle):
            raise LauncherWindowsError("stop-failed")
    finally:
        api.CloseHandle(handle)


class StartupRegistration:
    KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

    def __init__(self, name: str, command: str, key: str = KEY):
        import winreg
        self.registry = winreg
        self.name, self.command, self.key = name, command, key

    def read(self) -> str | None:
        reg = self.registry
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER, self.key, 0, reg.KEY_READ) as key:
                value, kind = reg.QueryValueEx(key, self.name)
            if kind != reg.REG_SZ or not isinstance(value, str):
                raise LauncherWindowsError("startup-entry-conflict")
            return value
        except FileNotFoundError:
            return None
        except OSError:
            raise LauncherWindowsError("startup-readback-failed") from None

    def enabled(self) -> bool:
        current = self.read()
        if current not in (None, self.command):
            raise LauncherWindowsError("startup-entry-conflict")
        return current is not None

    def set_enabled(self, enabled: bool):
        self.enabled()  # Do not overwrite an unexpected command under our name.
        reg = self.registry
        try:
            with reg.CreateKeyEx(reg.HKEY_CURRENT_USER, self.key, 0, reg.KEY_SET_VALUE) as key:
                if enabled:
                    if len(self.command) > 260:
                        raise LauncherWindowsError("startup-command-too-long")
                    reg.SetValueEx(key, self.name, 0, reg.REG_SZ, self.command)
                else:
                    try:
                        reg.DeleteValue(key, self.name)
                    except FileNotFoundError:
                        pass
            if self.enabled() != enabled:
                raise LauncherWindowsError("startup-readback-failed")
        except OSError:
            raise LauncherWindowsError("startup-save-failed") from None
