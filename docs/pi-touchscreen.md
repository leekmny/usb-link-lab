# Pi touchscreen and desktop launcher

## Pi 화면에서 시작

Pi 데스크톱 사용자로 로그인한 뒤 아래 명령을 저장소 루트에서 실행합니다. 한글 글꼴은 `fonts-noto-cjk`, 실행기는 Pi OS의 `chromium` 패키지를 사용합니다.

```bash
bash apps/raspberrypi/desktop/install-desktop.sh
```

바탕 화면 또는 응용 프로그램 메뉴에서 **USBLinkLab Capture**를 엽니다. 한국어 데스크톱에서는 **USBLinkLab 패킷 캡처**로 표시됩니다. 바탕 화면이 실행 권한을 물으면 **Allow Launching**을 선택합니다. 실행기는 현재 사용자의 Chromium 프로필을 별도로 생성하고, Pi의 `http://127.0.0.1:8765/`를 앱 창으로 엽니다. Wayland 세션에서는 Wayland 백엔드를 사용합니다.

1. 상단에서 Windows USB 링크, Pi 중계 서비스, iPhone USB 장치 인식을 각각 확인합니다.
2. **관측 지점**에서 Windows USB 또는 Pi 루프백을 선택하고 시간과 용량을 지정합니다. **캡처 시작**을 누른 뒤 Windows 또는 iPhone 앱에서 실험 데이터를 보냅니다.
3. **선택 지점 중지**를 누르거나 제한 시간까지 기다립니다. **캡처 기록**에서 저장된 파일을 다운로드하거나 **Pi에서 열기**로 실물 Pi의 Wireshark를 엽니다.
4. Pi의 그래픽 세션에서 Wireshark를 사용할 수 있으면 **Pi에서 실시간 보기**가 활성화됩니다. 추가 필터는 파일로 저장하는 캡처에 적용됩니다. iPhone USB 호스트 캡처는 비공개 진단 동의가 있어야 시작할 수 있습니다.

프로세스의 양방향 바이트와 표본 간 전송률을 화면에서 확인할 수 있습니다. **Pi 상태와 진단**에는 CPU 온도, 메모리, 스로틀링 비트, USB 멀티플렉서 상태가 표시됩니다. 확인할 수 없는 값은 **미확인**으로 표시합니다. USB 인식은 장치 열거 결과입니다. ULAB 인증과 실제 수신 여부는 양쪽 앱에서 확인합니다.

## Desktop integration

The installer runs as the desktop user, without sudo. It writes a launcher and the existing USBLinkLab application icon under `$XDG_DATA_HOME/usblinklab-desktop`, registers an application menu entry under `$XDG_DATA_HOME/applications`, and copies an executable entry to the XDG Desktop directory. Without XDG overrides the data path is `~/.local/share`. Running it again updates these files in place. To remove the launcher, remove those two desktop entries and the user-specific `usblinklab-desktop` directory. The browser profile lives under `$XDG_CONFIG_HOME/usblinklab/chromium`.

Start the dashboard HTTP service before launching the desktop icon. The white interface is Korean, touch-oriented and self-contained, with no remote fonts, scripts or images. The same HTML works through authenticated SSH port forwarding. Actions that open Wireshark target the Pi desktop when the server reports an available graphical session.

The optional browser regression test launches a fresh headless Chromium profile and intercepts API routes with synthetic test data. It exercises 720×1280 portrait, narrow mobile and desktop widths, unknown readings, USB diagnostics consent, CSRF-bearing capture commands and completed file actions. Supply Playwright Core and its matching Chromium, or set `CHROMIUM_PATH` to an installed Chromium-family executable.

```bash
PLAYWRIGHT_CORE=/absolute/path/to/playwright-core node tests/test_dashboard_touch.cjs
```

A browser fixture checks UI behavior. Physical capture and touch operation require the running Pi desktop and a real capture source.
