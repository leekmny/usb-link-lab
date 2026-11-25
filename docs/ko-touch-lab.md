# Pi 터치 화면으로 USB 통신 실험하기

Windows 앱에서 보호 방식을 골라 보내면, Pi는 USB로 받은 바이트를 iPhone 앱까지 전달합니다. Pi 화면에서 캡처를 시작하고 Wireshark로 열 수 있습니다. 먼저 두 앱의 연결과 샘플 텍스트 한 건을 확인한 뒤 이미지 실험으로 넘어가세요. 현재 장비에서 확인한 범위는 [검증표](acceptance.md)에 있습니다.

## 1. 장비 연결과 전원 확인

| 장비 또는 포트 | 연결·조작 | 성공 표시 |
| --- | --- | --- |
| 분리 어댑터의 **Power** | 어댑터 제조사가 지정한 외부 전원을 연결합니다. 어댑터와 분리기의 전압·전류 정격을 각각 확인합니다. | Pi가 안정적으로 부팅하고 부하 중에도 저전압 경고가 없습니다. |
| 분리 어댑터의 **PC** | Windows 노트북의 데이터 지원 USB 포트에 연결합니다. | 장치 관리자에서 Raspberry Pi USB 네트워크 어댑터가 정상 상태입니다. |
| 분리 어댑터의 **Device** | Pi 5의 USB-C 포트에 연결합니다. | Pi 가젯 인터페이스에 IPv4가 생기고 UDC가 `configured`를 표시합니다. |
| Pi USB-A | 데이터 케이블로 실험용 iPhone을 연결하고 잠금을 풉니다. | Pi가 Apple USB 기기를 인식합니다. iPhone에 **이 컴퓨터를 신뢰하겠습니까?**가 나타나면 Pi를 확인하고 신뢰를 누릅니다. |
| Pi DSI 화면 | 바탕화면에서 **USBLinkLab 패킷 캡처** 또는 **USBLinkLab Capture** 아이콘을 엽니다. | 720×1280 세로 화면에 **패킷 캡처**가 열립니다. Pi OS Lite에서는 노트북의 SSH 포워딩으로 같은 화면을 열 수 있습니다. |

전원이 불안정하면 iPhone 연결을 해제하고 정격과 케이블부터 확인하세요. 실험용 데이터 케이블을 뽑을 때도 Pi 외부 전원이 유지되는 구성을 사용합니다. 관리용 SSH는 별도 Wi-Fi나 유선 LAN 주소로 접속한 뒤 실제 경로를 확인합니다.

Windows PowerShell에서 아래 주소에는 Pi 화면의 가젯 IPv4를 입력합니다. 출력의 인터페이스·Windows 출발지·직결 서브넷을 대조하세요.

```powershell
$PiGadget = Read-Host 'Pi 가젯 IPv4'
Get-NetAdapter | Format-Table ifIndex, Name, InterfaceDescription, Status
Find-NetRoute -RemoteIPAddress $PiGadget
```

Pi 터미널에서 아래 명령으로 장치 상태를 확인합니다. USB 목록은 로컬 화면에서만 확인하세요.

```bash
ip -br -4 addr
for state in /sys/class/udc/*/state; do cat "$state"; done
lsusb
vcgencmd get_throttled
```

`get_throttled=0x0`은 관측 시점의 결과입니다. 이미지 전송 중에도 다시 확인하세요. 처음 준비하는 Pi는 official `rpi-usb-gadget`와 해당 Windows 드라이버의 설치 상태부터 확인하세요. 이미 부팅되는 카드는 그대로 사용합니다. [Pi 설치와 복구](relay-service.md)

## 2. Pi 설치와 iPhone 신뢰

Pi에서 통합 저장소 또는 Pi 소스 번들의 최상위 폴더로 이동합니다. 아래 첫 설치 명령은 배포판 패키지와 systemd 유닛을 설치합니다. 설정 단계에서 Windows 가젯 어댑터의 IPv4 주소를 입력합니다. iPhone이 여러 대면 화면에서 실험용 기기를 직접 선택하세요.

```bash
sudo bash apps/raspberrypi/install.sh
sudo python3 /opt/ulab/configure.py --pair --enable
sudo bash capture/install-observatory.sh "$(id -un)"
systemctl --user daemon-reload
systemctl --user enable --now ulab-dashboard
bash apps/raspberrypi/desktop/install-desktop.sh
```

- **iPhone이 잠겨 있다는 결과**가 나오면 본체에서 암호를 입력해 잠금을 풀고 화면을 켠 상태로 유지합니다. iPhone에 Pi의 신뢰 요청이 표시되면 승인한 뒤 설정 명령을 다시 실행합니다. 성공하면 Pi에 USB 전용 기기를 선택했다는 결과가 표시됩니다. 암호나 기기 식별자는 보고할 필요가 없습니다.
- **USB 목록에는 보이는데 멀티플렉서가 기기를 찾지 못하면** Pi 데스크톱의 카메라·사진 가져오기 창을 확인합니다. 해당 iPhone의 카메라 마운트만 해제하고 usbmuxd의 장치 수를 재확인합니다. 다른 저장장치의 마운트는 유지합니다. `idevice_id -l | wc -l`은 전체 식별자를 출력하지 않고 연결 대수만 보여줍니다.
- **아이콘이 실행 허용을 물으면** 직접 설치한 USBLinkLab 아이콘의 실행을 허용합니다. 데스크톱 사용자의 현재 세션에서 실행하세요. 캡처 권한은 별도 서비스에 있습니다.

Pi OS 데스크톱 자체를 한국어로 사용할 때는 데스크톱 사용자로 `sudo sh apps/raspberrypi/desktop/korean-locale.sh`를 실행합니다. 이 스크립트는 CJK 글꼴과 한국어 로캘을 설치하고 **시스템의 기본 언어**를 바꿉니다. AccountsService가 있으면 현재 데스크톱 계정의 언어도 설정합니다. 성공 출력은 `ko_KR.utf8`과 한글을 표시할 글꼴 이름입니다. **다음 로그인**부터 적용되므로 진행 중인 캡처와 전송을 마친 뒤 로그아웃하세요. 키보드 배열은 기존 설정을 사용합니다. [한국어 로캘 스크립트](../apps/raspberrypi/desktop/korean-locale.sh)

```bash
systemctl is-active ulab-iproxy ulab-relay ulab-capture
systemctl --user is-active ulab-dashboard
```

SSH로 같은 화면을 보려면 Windows에서 미리 검증한 SSH 호스트 별칭을 입력합니다. 포워딩을 실행 중인 창을 유지하고 브라우저에서 `http://127.0.0.1:8765/`를 여세요. 터치용 대시보드와 인증된 장치 상태는 동일하며, **Pi에서 열기**는 Pi의 그래픽 세션을 사용합니다.

```powershell
$PiManagement = Read-Host '호스트 키를 확인한 관리용 SSH 별칭'
ssh -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -N -L 127.0.0.1:8765:127.0.0.1:8765 $PiManagement
```

## 3. Windows와 iPhone 앱 인증

1. iPhone에서 이미 설치한 서명된 USBLinkLab을 열고 전경을 유지합니다. 새 설치가 필요하면 서명·설치·개발자 신뢰를 먼저 끝냅니다. [iPhone 설치 안내](ios-handoff.md)
2. Windows의 **설정 → 연결 경로 → Raspberry Pi USB 중계**를 고릅니다. Pi 가젯의 IPv4와 중계 포트(기본 `43123`)를 입력하고 **적용**을 누릅니다. 장치 목록에서 Windows 가젯 어댑터를 선택합니다.
3. 같은 iPhone의 기존 직결 페어링을 사용하려면 **기존 USB 직결 iPhone 페어링 사용 → 연결**을 선택합니다. 새 등록은 해당 옵션을 해제한 뒤 **페어링**을 누릅니다. Windows가 QR을 표시하면 iPhone에서 스캔하고 **준비**를 누른 다음 Windows에서 시작합니다. 6자리 방식도 양쪽 앱에서 같은 초대 방식을 선택합니다.
4. Windows에서 **iPhone 앱 인증됨**을 확인합니다. Pi 대시보드의 **USB 인식**은 커널이 폰을 본 단계입니다. 인증과 수신 완료는 양 끝 앱에서 확인합니다.

Windows 설정 한 장과 실제 전송 결과를 담은 화면 한 장이면 경로와 결과를 설명할 수 있습니다. QR, PIN, 신뢰 대화상자의 식별 정보가 보이는 시점에는 촬영하지 마세요.

## 4. Pi에서 캡처하고 Wireshark로 열기

최초 등록이 끝났으면 앱을 정상적으로 연결 해제합니다. **저장된 키로 다시 연결하기 전에 캡처를 시작**해야 디섹터가 ULAB 핸드셰이크부터 읽을 수 있습니다.

1. Pi 대시보드에서 **관측 지점**으로 Windows 쪽 USB 가젯을 고릅니다. 처음에는 30초, 8 MiB로 설정하고 **캡처 시작**을 누릅니다. 필요하면 `lo`의 iproxy 관측 지점을 별도 세션으로 시작합니다.
2. Windows에서 다시 **연결**하고, 예를 들어 `ULAB-LAB-001`같은 합성 문구를 입력합니다. 보호 방식을 명시적으로 선택하고 전송합니다. iPhone에서 수신을 확인한 뒤 반대 방향의 전송도 확인합니다.
3. **선택 지점 중지** 후 **캡처 기록 → Pi에서 열기**를 누릅니다. 실제 Wireshark가 뜨면 표시 필터 `ulab.mode == 0`과 `ulab.frame_type == 3`으로 평문 DATA를 찾습니다. AES-GCM은 `ulab.mode == 1`, ChaCha20-Poly1305는 `ulab.mode == 2`, HMAC은 `ulab.mode == 3`입니다.
4. 원본 PCAPNG는 **다운로드**로 보존합니다. USB 호스트 버스가 필요할 때만 usbmon 지점을 선택하고 비공개 진단 캡처에 동의합니다. 이 파일에는 해당 iPhone의 다른 호스트 요청도 들어갈 수 있습니다.

실행 중인 터치 대시보드의 실제 상태, 종료된 캡처의 패킷 수, Wireshark의 ULAB DATA 한 건을 선별해 남깁니다. 프로그램은 라이트 테마로, 개발용 VS Code 로그는 다크 테마로 촬영합니다. 프로젝트의 비공개 `기록/스크린샷/` 아래에 기기별로 저장하고 합성 UI·시뮬레이터·실기기 여부를 파일명이나 목록에 표시합니다. 촬영을 위해 새로 연 창은 작업 후 닫습니다. [Pi 터치 화면과 바탕화면 아이콘](pi-touchscreen.md)

Windows Wireshark로 같은 관측 지점을 실시간으로 보려면 통합 저장소 루트의 PowerShell에서 아래를 실행합니다. 네 변수에는 기존 SSH 설정의 실제 관리 호스트명, Pi 사용자명, 개인키 파일 **경로**, 검증된 known-hosts 파일 **경로**를 입력합니다. 파일 내용을 복사할 필요가 없습니다. 실행 후 Windows 앱에서 저장된 키로 연결해 전송하세요.

```powershell
$ManagementHost = Read-Host '관리용 호스트명 또는 주소'
$PiUser = Read-Host 'Pi 사용자명'
$KeyPath = Read-Host 'SSH 개인키 경로'
$KnownHostsPath = Read-Host '검증한 known-hosts 경로'
$CaptureFile = Join-Path $PWD ("lab-" + (Get-Date -Format yyyyMMdd-HHmmss) + ".pcapng")
./capture/Start-LabCapture.ps1 -ManagementHost $ManagementHost -User $PiUser -Key $KeyPath -KnownHosts $KnownHostsPath -Layer gadget -Seconds 30 -MiB 8 -Save $CaptureFile
```

위 명령은 SSH로 받은 PCAPNG 바이트를 Windows Wireshark로 전달합니다. 실험 페이로드는 가젯과 Pi USB-A 경로를 사용합니다. PowerShell 문자열 파이프로 캡처 바이트를 저장하지 마세요. [Wireshark 열기와 파일 복원](ulab-dissector.md)

## 5. 결과 기록과 원상복구

네 가지 보호 방식 각각에 대해 텍스트·생성한 PNG·생성한 바이너리 파일을 양방향으로 보내면 24건입니다. 실행한 경우에만 각 행에 방향·보호 방식·원본 크기·수신 결과·양 끝 SHA-256·캡처 파일 해시·완료 ACK까지 걸린 시간을 적습니다. 시간은 송신 측의 같은 단조 시계로 측정합니다. 실험을 중단한 항목은 별도 기록합니다. 이미지 원본과 수신본을 각각 `Get-FileHash -Algorithm SHA256` 또는 `sha256sum`으로 확인하고, 수신 측에서 꺼낸 파일의 해시를 사용하세요. [판정 기준](acceptance.md)

중단 시험은 먼저 실험 앱의 현재 전송 상태를 적고, Pi 전원을 유지한 채 선택한 데이터 선 한 가지만 분리합니다. 장치 목록 변화, 송·수신 상태와 Wireshark 시각을 남깁니다. 다시 연결하고 인증한 뒤 새 합성 항목을 보내 복구를 확인합니다. 기존 전송이 **미확인**인 경우 수신 저장소부터 확인하세요.

```bash
sudo bash apps/raspberrypi/rollback.sh
systemctl --user disable --now ulab-dashboard.service
sudo systemctl disable --now ulab-capture.service
```

세 명령은 순서대로 중계 서비스, 화면, 캡처 서비스를 정지합니다. 캡처만 끝내고 다음 실험을 계속할 때는 대시보드에서 해당 세션만 중지하면 됩니다. 서비스를 다시 켤 때는 기존 설정의 가젯 주소와 iPhone 선택을 확인하고 `sudo systemctl enable --now ulab-relay ulab-capture`, 현재 데스크톱 사용자로 `systemctl --user enable --now ulab-dashboard`를 실행합니다.
