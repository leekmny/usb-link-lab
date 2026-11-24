# Third-party notices

USBLinkLab contains unmodified, dynamically loaded Qt and Qt for Python
components. Qt Core, Gui, Network, Widgets, SVG, and image plugins, along with PySide6 and Shiboken, are distributed
under GNU LGPL 3.0 where offered by their respective sources. The LGPL and GPL
texts accompany the build in the licenses folder. See the module and
third-party attributions in the corresponding Qt source and documentation.

| Component | License and corresponding materials |
| --- | --- |
| Qt 6.10.2 Windows libraries and plugins | LGPL 3.0, GPL 3.0, and separately licensed third-party code. [Complete Qt 6.10.2 source archive](https://download.qt.io/official_releases/qt/6.10/6.10.2/single/qt-everywhere-src-6.10.2.tar.xz), [Qt licensing](https://doc.qt.io/qt-6.10/licensing.html), [module and third-party licenses](https://doc.qt.io/qt-6.10/licenses-used-in-qt.html), [Qt build instructions](https://doc.qt.io/qt-6.10/build-sources.html). |
| PySide6 Essentials and Shiboken 6.10.2 | LGPL 3.0 and GPL 3.0. [Complete official binding source and generator](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.10.2-src/pyside-setup-everywhere-src-6.10.2.tar.xz), [Windows build instructions](https://doc.qt.io/qtforpython-6.10/building_from_source/windows.html), [PySide6 wheel](https://pypi.org/project/PySide6-Essentials/6.10.2/), [Shiboken wheel](https://pypi.org/project/shiboken6/6.10.2/). |
| CPython 3.12.16 | PSF license and third-party notices in the Python license copy. [CPython 3.12.16 source](https://github.com/python/cpython/tree/v3.12.16), [python-build-standalone build recipes and distribution releases](https://github.com/astral-sh/python-build-standalone/releases), [OpenSSL 3.5.9 used by CPython](https://github.com/openssl/openssl/tree/openssl-3.5.9). |
| cryptography 50.0.0 and statically linked OpenSSL 4.0.1 | Apache 2.0 or BSD 3-Clause for cryptography, Apache 2.0 for OpenSSL. [cryptography source](https://github.com/pyca/cryptography/tree/50.0.0), [OpenSSL source and notices](https://github.com/openssl/openssl/tree/openssl-4.0.1). |
| libffi used by CPython ctypes | MIT-style license texts in licenses. Source for [standalone release 20261026](https://github.com/astral-sh/python-build-standalone/blob/20261026/cpython-windows/build.py) at [libffi 16fad485](https://github.com/python/cpython-source-deps/tree/16fad4855b3d8c03b5910e405ff3a04395b39a98), and [standalone release 20261110 selected by uv 0.13.8](https://github.com/astral-sh/python-build-standalone/blob/20261110/cpython-windows/build.py) at [libffi 73b247f](https://github.com/python/cpython-source-deps/tree/73b247f34ef3ae1859b8c2c34d321d34ebc5db15). RUNTIME-VERSIONS.json records the CPython build timestamp. |
| CFFI 2.1.1 | MIT. [Wheel and source](https://pypi.org/project/cffi/2.1.1/). |
| PyInstaller 6.19.0 bootloader | GPL 2.0 with the PyInstaller bootloader exception. [License and exception](https://pyinstaller.org/en/v6.19.0/license.html), [source](https://github.com/pyinstaller/pyinstaller/tree/v6.19.0). |

The above version-specific material is available at no charge. Both OpenSSL versions carry the verbatim OpenSSL license included here. The original
locked wheel hashes are in requirements-win.lock in the Windows source
directory. RUNTIME-VERSIONS.json records the versions used at build time.
RUNTIME-FILES.txt lists files placed in the portable executable directory
before the license and release materials were added.

To use a modified Qt or Qt for Python build under LGPL, keep a copy of the
bundle and replace the matching DLLs, Python modules, and plugins in
USBLinkLab/_internal/PySide6 and USBLinkLab/_internal/shiboken6. Qt DLLs may
also be in USBLinkLab/_internal. Use a binary-compatible Windows x64 build
with the same Qt/Python ABI. Launch USBLinkLab.exe from that directory.
The application permits replacement and the reverse engineering necessary to
debug modifications to the LGPL components. A source checkout can also rebuild
the portable layout using the packaged build procedure.

The app loads the Windows UCRT and the Microsoft Visual C++ runtime from
the host. Install the [Microsoft Visual C++ x64 Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
if the compatible runtime is absent. The operating-system Apple Mobile
Device Service and device drivers are separate Apple prerequisites.
The in-process compatibility adapter follows the libusbmuxd 2.1.1 wire
format references below. The portable runtime inventory identifies the
files included in the bundle. The above notices apply to third-party
components.

- [libusbmuxd 2.1.1 protocol definitions](https://github.com/libimobiledevice/libusbmuxd/blob/2.1.1/include/usbmuxd-proto.h)
- [libusbmuxd 2.1.1 Windows host address and request behavior](https://github.com/libimobiledevice/libusbmuxd/blob/2.1.1/src/libusbmuxd.c)
