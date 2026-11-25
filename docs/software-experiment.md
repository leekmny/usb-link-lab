# Software capture experiment

This experiment runs the Windows project's Python `Session` in both protocol roles on one Linux host. A separate child process runs the Raspberry Pi project's production `Relay.accept` and forwarding code. Its listener and upstream endpoint use Linux loopback. The harness supplies the loopback test listener, so production USB gadget admission, iproxy and the physical iPhone stay outside the scope of this result. The relay child receives the upstream port and status-file path. The endpoint process generates the fresh pairing secret after starting the child. The harness never exports session keys.

## Reproduce

Run from the repository root on Linux with Python 3.11+, the project's `cryptography` dependency, Wireshark's `tshark`, Lua support, and permission to capture on `lo` with `dumpcap`. Use the distribution's Wireshark capture group or equivalent capabilities on the dumpcap helper. Run Python and TShark as the ordinary test user. A dedicated network namespace containing only loopback gives the narrowest observation scope.

~~~bash
ULAB_RUN_LIVE_CAPTURE=1 python3 -m unittest discover -s tests -p test_experiment_capture.py -v
python3 tools/relay_experiment.py --out /tmp/ulab-run-01
~~~

Use `--kind text` or `--kind image` to select an item type. Select a subset with repeatable `--mode 0` through `--mode 3`. Each output directory must be new or empty. For each mode and type, the tool opens fresh client, relay and server sockets. It creates one genuine PCAPNG through dumpcap, filtered to the two dynamically reserved TCP listener ports and IPv4 loopback. Capture begins before the authenticated handshake. A 30-second and 4 MiB limit apply to each case. The client and server send the same synthetic source simultaneously in opposite directions.

The tool checks the independently persisted endpoint files, records the endpoint completion events and runs the dissector and extractor on the PCAPNG. It compares DATA on the front and upstream legs. For text, the observed byte sequence is the same unique per-run marker across all four modes. For images, the positive marker is the PNG signature and the stronger content check is the extracted file's exact bytes and SHA-256. Protected bodies require authenticated endpoint decryption in the two Python roles. A marker's absence alone establishes no cryptographic security property.

`comparison.json` records source digests, packet and capture sizes, aggregate DATA record bytes by leg/direction, capture ports, tool versions, sender completion time through the final ACK, receiver hashes and extraction outcomes. `endpoint_pair_process_cpu_ms` measures aggregate CPU time of the process running both endpoints over the simultaneous transfers. It includes encoding, storage and dispatch. It does not isolate cipher CPU cost. Compare repeated runs before interpreting timing differences. `relay.jsonl` contains the real relay connect/close events and direction byte counts. `endpoints.jsonl` contains completion events without secrets. The `extracted` directories hold verified visible content. A case can be rerun safely in a fresh directory after capture permission or interface errors.

## Recorded local run

On 2026-11-25, the test ran unprivileged in an isolated Linux network namespace under WSL2 using dumpcap/TShark 4.6.5 and Python 3.12.16. Only loopback was active. Eight captures contain 402 packets overall. The two roles each persisted identical source bytes in all eight cases. Windows TShark 4.6.10 reopened all eight PCAPNG files and independently recovered all visible marker and PNG bytes. Each encrypted case yielded four encrypted observations across the two legs and directions. The public archive includes the reviewed loopback captures, source files, endpoint and relay logs, checksums, and the independent Windows replay report. [Evidence](../evidence/software-loopback/comparison.json) · [Windows replay](../evidence/software-loopback/windows-replay.json)

To view a bundled case on Windows in Wireshark, read its recorded ports. The same commands work for each mode/type directory.

~~~powershell
$caseDir = 'evidence/software-loopback/0-text'
$config = Get-Content -Raw "$caseDir/relay-status.json" | ConvertFrom-Json
wireshark -X lua_script:dissectors/ulab.lua -o "ulab.port:$($config.listen_port)" -o "ulab.iproxy_port:$($config.upstream_port)" -r "$caseDir/loopback.pcapng" -Y ulab
~~~

## 한국어 실험 안내

`0-text`와 `3-text`에서 `ulab.frame_type == 3 && ulab.plaintext`를 적용하면 DATA 안에 같은 실험 문자열이 나타납니다. `1-text`와 `2-text`에서는 `ulab.ciphertext && ulab.aead_tag`로 보호된 본문과 태그를 확인할 수 있습니다. 이미지 원본은 `source.png`이며 18,569바이트입니다. 평문과 HMAC 모드는 캡처에서 복원한 파일까지 비교했고, AES-GCM과 ChaCha20-Poly1305는 양쪽 수신 프로그램이 복호화하고 저장한 파일의 SHA-256을 확인했습니다.

측정 환경은 리눅스 루프백입니다. 라즈베리 파이의 실제 USB 포트, iPhone 앱, usbmuxd 연결과 케이블을 분리했을 때의 동작은 별도의 하드웨어 실험으로 기록해야 합니다.
