#!/usr/bin/env python3
"""Windows processes for observed checks: a job object that names every process a command starts.

A command runs in its own job object. The job reports each process that joins
it through a completion port, and each one is named right away by its image
and command line, so an observation can tell which processes it saw from the
inside and which it did not. Closing the job ends whatever is still running.
Everything here uses documented Win32 calls and needs no administrator rights.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import subprocess
import threading
from typing import IO, Any, Mapping, Sequence

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    _ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined]
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)  # type: ignore[attr-defined]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    class _CompletionPort(ctypes.Structure):
        _fields_ = [("CompletionKey", ctypes.c_void_p), ("CompletionPort", wintypes.HANDLE)]

    class _Accounting(ctypes.Structure):
        _fields_ = [
            ("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
            ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
            ("TotalPageFaultCount", wintypes.DWORD), ("TotalProcesses", wintypes.DWORD),
            ("ActiveProcesses", wintypes.DWORD), ("TotalTerminatedProcesses", wintypes.DWORD),
        ]

    class _ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ThreadID", wintypes.DWORD),
            ("th32OwnerProcessID", wintypes.DWORD), ("tpBasePri", wintypes.LONG),
            ("tpDeltaPri", wintypes.LONG), ("dwFlags", wintypes.DWORD),
        ]

    class _UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT), ("Buffer", ctypes.c_void_p)]

    def _declare(function: Any, arguments: list[Any], result: Any) -> None:
        function.argtypes = arguments
        function.restype = result

    _declare(_kernel32.CreateJobObjectW, [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE)
    _declare(_kernel32.SetInformationJobObject, [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
             wintypes.BOOL)
    _declare(_kernel32.QueryInformationJobObject,
             [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)],
             wintypes.BOOL)
    _declare(_kernel32.AssignProcessToJobObject, [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL)
    _declare(_kernel32.TerminateJobObject, [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
    _declare(_kernel32.CreateIoCompletionPort, [wintypes.HANDLE, wintypes.HANDLE, ctypes.c_size_t, wintypes.DWORD],
             wintypes.HANDLE)
    _declare(_kernel32.GetQueuedCompletionStatus,
             [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(ctypes.c_size_t),
              ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD], wintypes.BOOL)
    _declare(_kernel32.PostQueuedCompletionStatus,
             [wintypes.HANDLE, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p], wintypes.BOOL)
    _declare(_kernel32.CreateToolhelp32Snapshot, [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE)
    _declare(_kernel32.Thread32First, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL)
    _declare(_kernel32.Thread32Next, [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL)
    _declare(_kernel32.OpenThread, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
    _declare(_kernel32.ResumeThread, [wintypes.HANDLE], wintypes.DWORD)
    _declare(_kernel32.OpenProcess, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE)
    _declare(_kernel32.GetExitCodeProcess, [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
    _declare(_kernel32.QueryFullProcessImageNameW,
             [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL)
    _declare(_kernel32.TerminateProcess, [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL)
    _declare(_kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL)
    _declare(_kernel32.GetCurrentProcess, [], wintypes.HANDLE)
    _declare(_kernel32.SetPriorityClass, [wintypes.HANDLE, wintypes.DWORD], wintypes.BOOL)
    _declare(_kernel32.LocalFree, [ctypes.c_void_p], ctypes.c_void_p)
    _declare(_ntdll.NtQueryInformationProcess,
             [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)],
             wintypes.LONG)
    _declare(_shell32.CommandLineToArgvW, [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)],
             ctypes.POINTER(wintypes.LPWSTR))

    _INVALID_HANDLE = ctypes.c_void_p(-1).value


_JOB_ACCOUNTING = 1
_JOB_COMPLETION_PORT = 7
_JOB_EXTENDED_LIMITS = 9
_KILL_ON_JOB_CLOSE = 0x2000
_LIMIT_PRIORITY_CLASS = 0x20
IDLE_PRIORITY_CLASS = 0x40
_MSG_ACTIVE_PROCESS_ZERO = 4
_MSG_NEW_PROCESS = 6
_MSG_STOP = 0xC11C  # posted by close(): not a job message
CREATE_SUSPENDED = 0x4
DETACHED_PROCESS = 0x8
CREATE_NEW_PROCESS_GROUP = 0x200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_SNAP_THREADS = 0x4
_THREAD_SUSPEND_RESUME = 0x2
_PROCESS_TERMINATE = 0x1
_PROCESS_QUERY_LIMITED = 0x1000
_STILL_ACTIVE = 259
_PROCESS_COMMAND_LINE = 60  # ProcessCommandLineInformation, Windows 8.1 and later


@dataclass
class JobProcess:
    pid: int
    image: str | None
    command_line: str | None


def _valid(handle: Any) -> bool:
    return bool(handle) and handle != _INVALID_HANDLE


def _open_process(pid: int, access: int = _PROCESS_QUERY_LIMITED) -> Any:
    handle = _kernel32.OpenProcess(access, False, pid)
    return handle if _valid(handle) else None


def _image(handle: Any) -> str | None:
    size = wintypes.DWORD(32768)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
        return None
    return buffer.value


def _command_line(handle: Any) -> str | None:
    length = wintypes.ULONG(0)
    _ntdll.NtQueryInformationProcess(handle, _PROCESS_COMMAND_LINE, None, 0, ctypes.byref(length))
    if not length.value:
        return None
    buffer = ctypes.create_string_buffer(length.value)
    if _ntdll.NtQueryInformationProcess(handle, _PROCESS_COMMAND_LINE, buffer, length, ctypes.byref(length)) != 0:
        return None
    text = _UnicodeString.from_buffer(buffer)
    if not text.Buffer:
        return ""
    return ctypes.wstring_at(text.Buffer, text.Length // 2)


def describe(pid: int) -> JobProcess:
    """The image and command line of a running process, as far as it can still be asked."""
    handle = _open_process(pid)
    if handle is None:
        return JobProcess(pid, None, None)
    try:
        return JobProcess(pid, _image(handle), _command_line(handle))
    finally:
        _kernel32.CloseHandle(handle)


def process_alive(pid: int) -> bool:
    handle = _open_process(pid)
    if handle is None:
        return False
    try:
        code = wintypes.DWORD(0)
        return bool(_kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == _STILL_ACTIVE
    finally:
        _kernel32.CloseHandle(handle)


def terminate(pid: int, code: int = 1) -> None:
    handle = _open_process(pid, _PROCESS_TERMINATE | _PROCESS_QUERY_LIMITED)
    if handle is None:
        return
    try:
        _kernel32.TerminateProcess(handle, code)
    finally:
        _kernel32.CloseHandle(handle)


def lower_priority() -> None:
    """Run the current process at idle CPU priority."""
    _kernel32.SetPriorityClass(_kernel32.GetCurrentProcess(), IDLE_PRIORITY_CLASS)


def split_command_line(text: str) -> list[str]:
    """argv as the C runtime splits a Windows command line."""
    if not text.strip():
        return []
    count = ctypes.c_int(0)
    argv = _shell32.CommandLineToArgvW(text, ctypes.byref(count))
    if not argv:
        return text.split()
    try:
        return [argv[index] for index in range(count.value)]
    finally:
        _kernel32.LocalFree(ctypes.cast(argv, ctypes.c_void_p))


def _resume(pid: int) -> None:
    snapshot = _kernel32.CreateToolhelp32Snapshot(_SNAP_THREADS, 0)
    if not _valid(snapshot):
        raise OSError(ctypes.get_last_error(), "cannot list the threads of the new process")
    try:
        entry = _ThreadEntry()
        entry.dwSize = ctypes.sizeof(_ThreadEntry)
        more = _kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == pid:
                thread = _kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, entry.th32ThreadID)
                if _valid(thread):
                    _kernel32.ResumeThread(thread)
                    _kernel32.CloseHandle(thread)
            more = _kernel32.Thread32Next(snapshot, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(snapshot)


class Job:
    """One command's processes: started inside the job, named as they join, ended together."""

    def __init__(self, *, idle: bool = False) -> None:
        self.handle = _kernel32.CreateJobObjectW(None, None)
        if not _valid(self.handle):
            raise OSError(ctypes.get_last_error(), "cannot create a job object")
        self.port = _kernel32.CreateIoCompletionPort(_INVALID_HANDLE, None, 0, 1)
        if not _valid(self.port):
            _kernel32.CloseHandle(self.handle)
            raise OSError(ctypes.get_last_error(), "cannot create a completion port")
        limits = _ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | (_LIMIT_PRIORITY_CLASS if idle else 0)
        limits.BasicLimitInformation.PriorityClass = IDLE_PRIORITY_CLASS if idle else 0
        association = _CompletionPort(None, self.port)
        if not (_kernel32.SetInformationJobObject(self.handle, _JOB_EXTENDED_LIMITS, ctypes.byref(limits),
                                                  ctypes.sizeof(limits))
                and _kernel32.SetInformationJobObject(self.handle, _JOB_COMPLETION_PORT,
                                                      ctypes.byref(association), ctypes.sizeof(association))):
            error = ctypes.get_last_error()
            self.close()
            raise OSError(error, "cannot configure the job object")
        self.members: dict[int, JobProcess] = {}
        self.empty = threading.Event()
        self._lock = threading.Lock()
        self._listener = threading.Thread(target=self._listen, daemon=True)
        self._listener.start()

    def _listen(self) -> None:
        message = wintypes.DWORD(0)
        key = ctypes.c_size_t(0)
        overlapped = ctypes.c_void_p(None)
        while True:
            if not _kernel32.GetQueuedCompletionStatus(self.port, ctypes.byref(message), ctypes.byref(key),
                                                       ctypes.byref(overlapped), 0xFFFFFFFF):
                if overlapped.value is None:
                    return  # the port was closed
                continue
            if message.value == _MSG_STOP:
                return
            if message.value == _MSG_NEW_PROCESS:
                pid = int(overlapped.value or 0)
                # Named at once: a short-lived process may be gone a moment later.
                described = describe(pid)
                with self._lock:
                    self.members[pid] = described
            elif message.value == _MSG_ACTIVE_PROCESS_ZERO:
                self.empty.set()

    def start(self, argv: Sequence[str], *, cwd: str, environment: Mapping[str, str] | None,
              stdout: int | IO[Any] | None = None, stderr: int | IO[Any] | None = None) -> subprocess.Popen:
        """Start ``argv`` suspended, put it in the job, then let it run: no child escapes."""
        process = subprocess.Popen(list(argv), cwd=cwd, env=dict(environment) if environment is not None else None,
                                   stdout=stdout, stderr=stderr, creationflags=CREATE_SUSPENDED)
        handle = int(process._handle)  # type: ignore[attr-defined]
        if not _kernel32.AssignProcessToJobObject(self.handle, handle):
            error = ctypes.get_last_error()
            _kernel32.TerminateProcess(handle, 1)
            process.wait()
            raise OSError(error, "cannot put the command in its job object")
        self.empty.clear()
        _resume(process.pid)
        return process

    def total(self) -> int:
        """How many processes ever joined the job."""
        info = _Accounting()
        if not _kernel32.QueryInformationJobObject(self.handle, _JOB_ACCOUNTING, ctypes.byref(info),
                                                   ctypes.sizeof(info), None):
            return -1
        return int(info.TotalProcesses)

    def active(self) -> int:
        info = _Accounting()
        if not _kernel32.QueryInformationJobObject(self.handle, _JOB_ACCOUNTING, ctypes.byref(info),
                                                   ctypes.sizeof(info), None):
            return -1
        return int(info.ActiveProcesses)

    def processes(self) -> list[JobProcess]:
        with self._lock:
            return list(self.members.values())

    def terminate(self, code: int = 1) -> None:
        _kernel32.TerminateJobObject(self.handle, code)

    def close(self) -> None:
        """End what still runs in the job and release it."""
        if getattr(self, "port", None) and _valid(self.port):
            _kernel32.PostQueuedCompletionStatus(self.port, _MSG_STOP, 0, None)
            listener = getattr(self, "_listener", None)
            if listener is not None:
                listener.join(timeout=2)
        for name in ("handle", "port"):
            handle = getattr(self, name, None)
            if handle is not None and _valid(handle):
                _kernel32.CloseHandle(handle)
                setattr(self, name, None)
