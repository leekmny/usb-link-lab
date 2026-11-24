"""Measure AES-256-GCM transfers between two Python endpoints on Windows loopback."""

import argparse
import asyncio
import ctypes
import hashlib
import json
import os
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from test_session import connected_pair
from usblinklab.protocol import Mode
from usblinklab.storage import Storage


class MemoryCounters(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("faults", wintypes.DWORD), ("peak", ctypes.c_size_t)] + [
        (f"size_{position}", ctypes.c_size_t) for position in range(7)
    ]


def peak_working_set():
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.restype = wintypes.HANDLE
    info = ctypes.windll.psapi.GetProcessMemoryInfo
    info.argtypes = (wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD)
    counters = MemoryCounters()
    counters.length = ctypes.sizeof(counters)
    if not info(process(), ctypes.byref(counters), counters.length):
        raise ctypes.WinError()
    return counters.peak


async def main(mib):
    with tempfile.TemporaryDirectory(prefix="usblinklab-stream-") as scratch:
        directory = Path(scratch)
        source = directory / "synthetic.bin"
        megabyte = 1024 * 1024
        block = os.urandom(megabyte)
        expected = hashlib.sha256()
        with source.open("wb") as file:
            for _ in range(mib):
                file.write(block)
                expected.update(block)
        del block

        a_store, b_store = Storage(directory / "sender"), Storage(directory / "receiver")
        start = time.perf_counter()
        job = a_store.stage_file(source, Mode.AES256GCM)
        stage_seconds = time.perf_counter() - start
        a, b = await connected_pair(a_store, b_store, lambda _: None, lambda _: None)
        runs = [asyncio.create_task(a.run()), asyncio.create_task(b.run())]
        try:
            await a.wait_ready()
            start = time.perf_counter()
            await a.send(job)
            transfer_seconds = time.perf_counter() - start
            [saved] = b_store.received()
            with saved.path.open("rb") as file:
                verified = hashlib.file_digest(file, "sha256").hexdigest()
            assert saved.size == mib * megabyte
            assert saved.sha256 == verified == expected.hexdigest()
            print(json.dumps({
                "transport": "Windows TCP loopback; both Python endpoints in one process",
                "mode": job.mode.name, "bytes": saved.size, "sha256_equal": True,
                "staging_seconds": round(stage_seconds, 3),
                "send_to_complete_ack_seconds": round(transfer_seconds, 3),
                "peak_process_working_set_bytes": peak_working_set(),
            }), flush=True)
        finally:
            await asyncio.gather(a.close(), b.close())
            await asyncio.gather(*runs, return_exceptions=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("size_mib", type=int, help="Synthetic file size in MiB (1 to 1024)")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows is required for process working-set measurement")
    if not 1 <= args.size_mib <= 1024:
        parser.error("size_mib must be between 1 and 1024")
    asyncio.run(main(args.size_mib))