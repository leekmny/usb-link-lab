# Windows streaming evidence

The Windows Python endpoints transferred synthetic files over loopback TCP using AES-256-GCM. The source, staged snapshot, and received file were temporary. For each run, the SHA-256 calculated while generating the source matched the saved metadata and an independent hash read from the received file.

| File size | Staging | Send through complete ACK | Peak process working set |
| --- | ---: | ---: | ---: |
| 100 MiB (104,857,600 bytes) | 0.219 s | 1.578 s | 43,003,904 bytes |
| 1 GiB (1,073,741,824 bytes) | 1.405 s | 18.574 s | 45,281,280 bytes |

Each size ran in a fresh Python process containing both endpoints. Windows reported the peak working set for the whole process via GetProcessMemoryInfo. Transfer timing started after the local handshake. These measurements cover Windows loopback TCP. Swift interoperability and physical USB remain separate verification steps.

## Reproduce

Run from the repository root on Windows PowerShell after installing the Windows development dependencies, including pytest. Allow roughly three times the chosen file size in the Windows temporary directory for the source, staged copy, and received file. The script accepts a file size from 1 to 1024 MiB and prints a JSON result.

~~~powershell
$env:PYTHONPATH = 'apps/windows;apps/windows/tests'
.venv/Scripts/python.exe tools/windows_stream_benchmark.py 100
.venv/Scripts/python.exe tools/windows_stream_benchmark.py 1024
~~~