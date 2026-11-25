"""Minimal Windows service status and IPv4 TCP owner queries."""

import ctypes as C
import socket
import sys
from ctypes import wintypes as W


_SERVICE = "Apple Mobile Device Service"
_SCM_CONNECT = 1
_SERVICE_QUERY_STATUS = 4
_SERVICE_STOPPED = 1
_SERVICE_RUNNING = 4
_ERROR_INSUFFICIENT_BUFFER = 122
_ERROR_SERVICE_DOES_NOT_EXIST = 1060
_TCP_OWNER_PID_CONNECTIONS = 4
_TCP_ESTABLISHED = 5
_MAX_TCP_TABLE = 8 * 1024 * 1024
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800


class WindowsUSBError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _ServiceStatus(C.Structure):
    _fields_ = [
        ("service_type", W.DWORD), ("current_state", W.DWORD),
        ("controls_accepted", W.DWORD), ("win32_exit_code", W.DWORD),
        ("service_exit_code", W.DWORD), ("checkpoint", W.DWORD),
        ("wait_hint", W.DWORD), ("process_id", W.DWORD), ("service_flags", W.DWORD),
    ]


class _TcpRow(C.Structure):
    _fields_ = [
        ("state", W.DWORD), ("local_address", W.DWORD), ("local_port", W.DWORD),
        ("remote_address", W.DWORD), ("remote_port", W.DWORD), ("process_id", W.DWORD),
    ]


class _TcpTable(C.Structure):
    _fields_ = [("count", W.DWORD), ("first_row", _TcpRow)]


def service_pid() -> int:
    if sys.platform != "win32":
        raise WindowsUSBError("service_unavailable")
    api = C.WinDLL("Advapi32.dll", use_last_error=True, winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
    api.OpenSCManagerW.argtypes = [W.LPCWSTR, W.LPCWSTR, W.DWORD]
    api.OpenSCManagerW.restype = W.HANDLE
    api.OpenServiceW.argtypes = [W.HANDLE, W.LPCWSTR, W.DWORD]
    api.OpenServiceW.restype = W.HANDLE
    api.QueryServiceStatusEx.argtypes = [
        W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)
    ]
    api.QueryServiceStatusEx.restype = W.BOOL
    api.CloseServiceHandle.argtypes = [W.HANDLE]
    api.CloseServiceHandle.restype = W.BOOL

    manager = api.OpenSCManagerW(None, None, _SCM_CONNECT)
    if not manager:
        raise WindowsUSBError("service_unavailable")
    try:
        service = api.OpenServiceW(manager, _SERVICE, _SERVICE_QUERY_STATUS)
        if not service:
            if C.get_last_error() == _ERROR_SERVICE_DOES_NOT_EXIST:
                raise WindowsUSBError("service_missing")
            raise WindowsUSBError("service_unavailable")
        try:
            status = _ServiceStatus()
            received = W.DWORD()
            if not api.QueryServiceStatusEx(service, 0, C.byref(status), C.sizeof(status),
                                            C.byref(received)):
                raise WindowsUSBError("service_unavailable")
        finally:
            api.CloseServiceHandle(service)
    finally:
        api.CloseServiceHandle(manager)

    if status.current_state == _SERVICE_STOPPED:
        raise WindowsUSBError("service_stopped")
    if status.current_state != _SERVICE_RUNNING or not status.process_id:
        raise WindowsUSBError("service_unavailable")
    return status.process_id


def connected_owner(local: tuple[str, int], remote: tuple[str, int]) -> int | None:
    """Look up the PID of the Windows service side of an established socket."""
    api = C.WinDLL("Iphlpapi.dll", use_last_error=True, winmode=_LOAD_LIBRARY_SEARCH_SYSTEM32)
    query = api.GetExtendedTcpTable
    query.argtypes = [C.c_void_p, C.POINTER(W.DWORD), W.BOOL, W.ULONG, C.c_int, W.ULONG]
    query.restype = W.DWORD

    size = W.DWORD()
    buffer = None
    result = query(None, C.byref(size), False, socket.AF_INET, _TCP_OWNER_PID_CONNECTIONS, 0)
    for _ in range(3):
        if result != _ERROR_INSUFFICIENT_BUFFER:
            break
        if not _TcpTable.first_row.offset <= size.value <= _MAX_TCP_TABLE:
            raise WindowsUSBError("mux_unavailable")
        buffer = C.create_string_buffer(size.value)
        result = query(buffer, C.byref(size), False, socket.AF_INET, _TCP_OWNER_PID_CONNECTIONS, 0)
        if result == 0:
            break
    if result != 0 or buffer is None:
        raise WindowsUSBError("mux_unavailable")

    count = W.DWORD.from_buffer(buffer).value
    offset = _TcpTable.first_row.offset
    if count > (len(buffer) - offset) // C.sizeof(_TcpRow):
        raise WindowsUSBError("mux_unavailable")
    rows = (_TcpRow * count).from_buffer(buffer, offset)
    local_address, remote_address = (int.from_bytes(socket.inet_aton(item[0]), "little")
                                     for item in (local, remote))
    local_port, remote_port = (socket.htons(item[1]) for item in (local, remote))
    for row in rows:
        if (row.state == _TCP_ESTABLISHED
                and (row.local_address, row.local_port & 0xFFFF) == (local_address, local_port)
                and (row.remote_address, row.remote_port & 0xFFFF) == (remote_address, remote_port)):
            return row.process_id
    return None
