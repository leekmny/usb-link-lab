# USBLinkLab for Windows

Extract the ZIP and open USBLinkLab.exe from the extracted folder. Keep the
_internal folder beside the executable. This Windows x64 build includes its
Python and Qt runtime. BUILD-INFO.json identifies the source, protocol revision,
and executable signature status. Verify the archive with SHA256SUMS from the
same release. Windows may show SmartScreen for unsigned builds.

Install the [Microsoft Visual C++ x64 Redistributable](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist)
if Windows does not already have the compatible runtime. Install Apple
Devices separately from Microsoft Store. Open it to verify that
Windows recognizes the unlocked iPhone attached by a data-capable USB cable.
Approve Trust This Computer if the iPhone requests it. If the device is attached
to WSL by usbipd, detach it before trying USBLinkLab. Run the companion iPhone
app in the foreground and start its listener before connecting from Windows.

On both updated endpoints, choose app pairing on the selected device. Scan
the Windows QR in the iPhone app, or choose the six-digit code on both
devices. Prepare on the iPhone and then start pairing from Windows.
Invitations expire after two minutes and can be used once. Transmit
synthetic data first. An Apple USB discovery result confirms a device record.
A successful socket connection confirms that the selected USB path opened.
Full verification requires an authenticated session and the physical
wireless-disabled and unplug tests with the companion app.

Application authentication and protection are defined in PROTOCOL_V1.md.
Plaintext offers neither content confidentiality nor content authentication.
HMAC-SHA256 authenticates content and leaves it readable. Locally stored
history and exported files may contain original content. This software is for
an educational laboratory.

THIRD-PARTY-NOTICES.md identifies the bundled dependencies, licenses,
corresponding source, and replacement instructions.
