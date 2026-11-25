"""Native desktop conversation, pairing and transfer details."""

from math import ceil
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QSize, Qt, QSettings, QTimer
from PySide6.QtGui import (
    QColor, QFont, QFontDatabase, QIcon, QImage, QImageReader, QKeySequence, QPalette,
    QPainter, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QMainWindow, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from . import PROTOCOL_REVISION, __version__
from .icons import glyph, icon
from .labels import COPY
from .protocol import Mode
from .theme import THEMES, stylesheet


ACTIVE_CONNECTIONS = {"connecting", "pairing_code_connecting", "connected"}


def label(text="", name="", *, wrap=False, size=None):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    if size:
        font = widget.font()
        font.setPixelSize(round(size * 4 / 3))
        font.setWeight(QFont.Weight.DemiBold)
        widget.setFont(font)
    return widget


def byte_count(value):
    if value < 1024:
        return f"{value} B"
    for unit in ("KiB", "MiB", "GiB"):
        value /= 1024
        if value < 1024 or unit == "GiB":
            return f"{value:,.1f} {unit}"


def pairing_qr_pixmap(payload, pixel_ratio):
    import segno

    matrix = segno.make_qr(payload, error="M").matrix
    quiet_zone = 4
    modules = len(matrix) + 2 * quiet_zone
    scale = max(1, int(216 * pixel_ratio) // modules)
    pixels = modules * scale
    image = QImage(pixels, pixels, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    with QPainter(image) as painter:
        for y, row in enumerate(matrix, quiet_zone):
            for x, dark in enumerate(row, quiet_zone):
                if dark:
                    painter.fillRect(x * scale, y * scale, scale, scale, Qt.GlobalColor.black)
    pixmap = QPixmap.fromImage(image)
    pixmap.setDevicePixelRatio(pixel_ratio)
    return pixmap


class Selector(QComboBox):
    def paintEvent(self, event):
        super().paintEvent(event)
        color = self.palette().color(QPalette.ColorRole.WindowText)
        pixmap = glyph("chevron", color, 13)
        painter = QPainter(self)
        painter.drawPixmap(self.width() - 23, (self.height() - 13) // 2, pixmap)
        painter.end()


class PairingDialog(QDialog):
    def __init__(self, window, device):
        super().__init__(window, objectName="pairingDialog")
        self.device = device
        self.request_id = None
        self.method = "qr"
        self.tr_key = window.tr_key
        self.submit = window.bridge.submit
        self._awaiting_result = False
        self._deadline = 0
        self.setWindowTitle(self.tr_key("pair"))
        self.setMinimumWidth(440)
        body = QVBoxLayout(self)
        body.setContentsMargins(22, 20, 22, 18)
        body.setSpacing(12)
        self.intro = label(wrap=True)
        body.addWidget(self.intro)

        self.code = label(name="pairingCode")
        self.code.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.code.setMinimumHeight(66 if window.large else 58)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPixelSize(42 if window.large else 36)
        font.setWeight(QFont.Weight.DemiBold)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4)
        self.code.setFont(font)
        colors = window.colors
        self.qr = label(name="pairingQr")
        self.qr.setAccessibleName(self.tr_key("pair_qr_image"))
        self.qr.setMinimumHeight(232)
        for display in (self.qr, self.code):
            display.setAlignment(Qt.AlignmentFlag.AlignCenter)
            display.setStyleSheet(
                f"background: {colors['surface']}; border: 1px solid {colors['line']}; border-radius: 7px;"
            )
            body.addWidget(display)
        self.status = label(name="muted", wrap=True)
        body.addWidget(self.status)
        self.switch = window._button("use_pair_code", self._switch_method, name="quiet")
        self.switch.setAutoDefault(False)
        body.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignLeft)

        buttons = QHBoxLayout()
        self.refresh = window._button("new_pair_qr", self.request_pairing)
        self.refresh.setAutoDefault(False)
        cancel = window._button("cancel_pair", self.reject)
        cancel.setAutoDefault(False)
        self.start = window._button("start_pair", self._start_pairing, name="primary")
        self.start.setDefault(True)
        buttons.addWidget(self.refresh)
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(self.start)
        body.addLayout(buttons)
        cancel.setFocus()

        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._update_countdown)
        self._render_method()

    @property
    def request_identity(self):
        return self.device, self.request_id

    def request_pairing(self):
        self.clear_pairing()
        self.request_id = uuid4().hex
        self._awaiting_result = True
        self.status.setText(self.tr_key("pairing_code_generating"))
        self.refresh.setEnabled(False)
        self.submit(f"new_pair_{self.method}", *self.request_identity)

    def show_pairing(self, topic, value):
        if (not self._awaiting_result or topic != f"pairing_{self.method}" or
                (value["device"], value["request_id"]) != self.request_identity):
            return
        deadline = value["expires_at"]
        if deadline <= monotonic():
            self.show_unavailable("pairing_code_expired")
            return
        if self.method == "qr":
            payload = value["payload"]
            if not payload.startswith("ULABQR1:"):
                self.show_unavailable("pairing_code_failed")
                return
            try:
                self.qr.setPixmap(pairing_qr_pixmap(payload, self.devicePixelRatioF()))
            except (ImportError, ValueError):
                self.show_unavailable("pairing_code_failed")
                return
        else:
            code = value["code"]
            if len(code) != 6 or not code.isascii() or not code.isdecimal():
                self.show_unavailable("pairing_code_failed")
                return
            self.code.setText(code)
        self._awaiting_result = False
        self._deadline = deadline
        self.refresh.setEnabled(True)
        self.start.setEnabled(True)
        self._timer.start()
        self._update_countdown()

    def clear_pairing(self):
        self._awaiting_result = False
        self._deadline = 0
        self._timer.stop()
        self.code.clear()
        self.qr.clear()
        self.start.setEnabled(False)

    def show_unavailable(self, reason):
        self.clear_pairing()
        self.status.setText(self.tr_key(reason))
        self.refresh.setEnabled(True)

    def _render_method(self):
        use_qr = self.method == "qr"
        self.intro.setText(self.tr_key("pair_qr_intro" if use_qr else "pair_code_intro"))
        self.qr.setVisible(use_qr)
        self.code.setVisible(not use_qr)
        for button, key in ((self.refresh, f"new_pair_{self.method}"),
                            (self.switch, "use_pair_code" if use_qr else "use_pair_qr")):
            button.setProperty("copyKey", key)
            button.setText(self.tr_key(key))

    def _switch_method(self):
        self.method = "code" if self.method == "qr" else "qr"
        self._render_method()
        self.request_pairing()
        self.adjustSize()

    def _update_countdown(self):
        remaining = ceil(self._deadline - monotonic())
        if remaining <= 0:
            self.show_unavailable("pairing_code_expired")
        else:
            self.status.setText(self.tr_key("pairing_code_remaining").format(seconds=remaining))

    def _start_pairing(self):
        self._update_countdown()
        if self.start.isEnabled():
            self.accept()


class Window(QMainWindow):
    def __init__(self, bridge, root):
        super().__init__()
        self.bridge = bridge
        self.preferences = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        self.language = self.preferences.value("language", 0, type=int) % 2
        self.theme = self.preferences.value("theme", "light")
        self.large = self.preferences.value("large", False, type=bool)
        self.setWindowTitle("USBLinkLab")
        self.setMinimumSize(790, 600)
        self.resize(1110, 750)
        self.setAcceptDrops(True)
        self.rows = {}
        self.details_dialog = None
        self.pairing_dialog = None
        self.pairing_attempt = None
        self.attachments = []
        self.connection_state = "disconnected"
        self.feedback = ""
        self.feedback_type = "muted"
        self.staging = False
        self.show_history = False
        self._build()
        self._apply_appearance()
        self._retranslate()
        self.bridge.changed.connect(self._update)

    def tr_key(self, key):
        return COPY.get(key, (key, key))[self.language]

    def mode_name(self, mode):
        return {
            Mode.PLAINTEXT: self.tr_key("plaintext"), Mode.AES256GCM: "AES-256-GCM",
            Mode.CHACHA20POLY1305: "ChaCha20-Poly1305", Mode.HMACSHA256: "HMAC-SHA256",
        }[Mode(mode)]

    def mode_meaning(self, mode):
        return self.tr_key({0: "unauthenticated", 3: "authenticated"}.get(mode, "encrypted"))

    def _button(self, key, callback=None, *, icon=None, name=""):
        button = QPushButton(self.tr_key(key), objectName=name)
        button.setProperty("copyKey", key)
        if icon is not None:
            button.setProperty("iconKey", icon)
            button.setIconSize(QSize(17, 17))
        if callback is not None:
            button.clicked.connect(callback)
        return button

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.sidebar = QWidget(objectName="sidebar")
        side = QVBoxLayout(self.sidebar)
        side.setSpacing(8)
        side.setContentsMargins(16, 19, 16, 14)
        brand = QHBoxLayout()
        self.logo = label()
        self.logo.setFixedSize(32, 32)
        self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.addWidget(self.logo)
        brand.addWidget(label("USBLinkLab", size=14), 1)
        side.addLayout(brand)
        side.addSpacing(22)
        self.conversation = self._button("conversation", lambda: self._view(False),
                                         icon="chat", name="navigation")
        self.history = self._button("history", lambda: self._view(True),
                                    icon="list", name="navigation")
        for navigation in (self.conversation, self.history):
            navigation.setCheckable(True)
            side.addWidget(navigation)
        side.addSpacing(20)
        device_header = QHBoxLayout()
        self.device_title = label(size=11)
        self.refresh = self._button("refresh", lambda: self.bridge.submit("discover"),
                                    icon="refresh", name="iconButton")
        self.refresh.setFixedSize(34, 30)
        device_header.addWidget(self.device_title, 1)
        device_header.addWidget(self.refresh)
        side.addLayout(device_header)
        self.devices = Selector()
        side.addWidget(self.devices)
        self.pair = self._button("pair_short", self._pair_dialog, name="quiet")
        self.connect = self._button("connect", self._toggle_connection, name="secondary")
        device_actions = QHBoxLayout()
        device_actions.setSpacing(6)
        device_actions.addWidget(self.connect, 1)
        device_actions.addWidget(self.pair)
        side.addLayout(device_actions)
        side.addSpacing(10)
        self.connection_caption = label(size=10, wrap=True)
        self.connection_status = label(name="muted", wrap=True)
        side.addWidget(self.connection_caption)
        side.addWidget(self.connection_status)
        side.addStretch()
        side.addWidget(self._button("settings", self._settings_dialog, icon="settings", name="navigation"))
        layout.addWidget(self.sidebar)

        workspace = QWidget(objectName="workspace")
        view = QVBoxLayout(workspace)
        view.setContentsMargins(26, 24, 26, 15)
        view.setSpacing(10)
        title_row = QHBoxLayout()
        self.title = label(size=23)
        self.details_toggle = self._button("details", self._toggle_details, icon="details", name="quiet")
        self.details_toggle.setCheckable(True)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.details_toggle)
        view.addLayout(title_row)
        self.subtitle = label(name="muted", wrap=True)
        view.addWidget(self.subtitle)
        self.empty = QWidget()
        empty_layout = QVBoxLayout(self.empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_image = label()
        self.empty_image.setFixedSize(49, 49)
        self.empty_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_image, 0, Qt.AlignmentFlag.AlignCenter)
        empty_layout.addSpacing(5)
        self.empty_title = label(size=15)
        self.empty_body = label(name="muted", wrap=True)
        self.empty_body.setMaximumWidth(420)
        for widget in (self.empty_title, self.empty_body):
            widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(widget)
        view.addWidget(self.empty, 1)

        self.items = QTreeWidget()
        self.items.setRootIsDecorated(False)
        self.items.setAlternatingRowColors(True)
        self.items.setAllColumnsShowFocus(True)
        self.items.setUniformRowHeights(True)
        self.items.setColumnCount(4)
        header = self.items.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.items.itemSelectionChanged.connect(self._show_detail)
        self.items.itemActivated.connect(lambda *_: self._open_details())
        view.addWidget(self.items, 2)
        self.items.hide()

        self.compose = QFrame(objectName="compose")
        composer = QVBoxLayout(self.compose)
        composer.setSpacing(8)
        composer.setContentsMargins(14, 12, 14, 12)
        compose_heading = QHBoxLayout()
        self.compose_title = label(size=11)
        self.recipient = label(name="muted")
        compose_heading.addWidget(self.compose_title, 1)
        compose_heading.addWidget(self.recipient)
        composer.addLayout(compose_heading)
        self.message = QPlainTextEdit()
        self.message.setTabChangesFocus(True)
        composer.addWidget(self.message)
        self.tray = QTreeWidget()
        self.tray.setHeaderHidden(True)
        self.tray.setRootIsDecorated(False)
        self.tray.setMaximumHeight(100)
        self.tray.hide()
        composer.addWidget(self.tray)
        attachments = QHBoxLayout()
        self.add_image = self._button("image", lambda: self._choose_files("image"),
                                      icon="image", name="quiet")
        self.add_file = self._button("file", lambda: self._choose_files("file"),
                                     icon="file", name="quiet")
        self.clear_files = self._button("remove_files", self._clear_files, name="quiet")
        attachments.addWidget(self.add_image)
        attachments.addWidget(self.add_file)
        attachments.addStretch()
        attachments.addWidget(self.clear_files)
        composer.addLayout(attachments)
        controls = QHBoxLayout()
        self.mode_label = label()
        self.mode = Selector()
        self.mode_label.setBuddy(self.mode)
        self.send = self._button("send", self._enqueue, icon="send", name="primary")
        controls.addWidget(self.mode_label)
        controls.addWidget(self.mode)
        controls.addStretch()
        controls.addWidget(self.send)
        composer.addLayout(controls)
        self.exposure = label(name="exposure", wrap=True)
        self.compose_hint = label(name="muted", wrap=True)
        composer.addWidget(self.exposure)
        composer.addWidget(self.compose_hint)
        view.addWidget(self.compose)

        self.feedback_label = label(wrap=True)
        view.addWidget(self.feedback_label)
        self.diagnostic_toggle = self._button("diagnostics", icon="diagnostics", name="quiet")
        self.diagnostic_toggle.setCheckable(True)
        view.addWidget(self.diagnostic_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.diagnostic = QFrame(objectName="diagnostics")
        diagnostic_layout = QVBoxLayout(self.diagnostic)
        self.diagnostic_text = label(name="muted", wrap=True)
        self.diagnostic_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        diagnostic_layout.addWidget(self.diagnostic_text)
        self.diagnostic.hide()
        self.diagnostic_toggle.toggled.connect(self.diagnostic.setVisible)
        view.addWidget(self.diagnostic)
        layout.addWidget(workspace, 1)

        self.inspector = QWidget(objectName="inspector")
        inspector = QVBoxLayout(self.inspector)
        inspector.setContentsMargins(16, 24, 16, 18)
        inspector.setSpacing(10)
        self.detail_heading = label(size=15)
        self.detail_name = label(wrap=True, size=12)
        self.detail_security = label(wrap=True)
        self.detail_status = label(name="muted", wrap=True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.preview = label(wrap=True)
        self.preview.setMaximumHeight(240)
        self.preview.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.retry = self._button("retry", self._retry_item)
        self.remove = self._button("remove", self._remove_item)
        self.delete_message = self._button("delete_message", self._delete_message)
        self.export = self._button("export", self._export_item)
        self.cancel = self._button("cancel", self._cancel_session)
        for widget in (self.detail_heading, self.detail_name, self.detail_security, self.detail_status,
                       self.progress, self.preview, self.retry, self.remove, self.delete_message,
                       self.export, self.cancel):
            inspector.addWidget(widget)
        inspector.addStretch()
        self.root_layout = layout
        layout.addWidget(self.inspector)
        self.inspector.hide()
        self._send_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.send.click)
        self.devices.currentIndexChanged.connect(self._refresh_controls)
        self.mode.currentIndexChanged.connect(self._refresh_controls)
        self.message.textChanged.connect(self._refresh_controls)

    def _apply_appearance(self):
        self.colors = THEMES.get(self.theme, THEMES["light"])
        families = QFontDatabase.families()
        font = QFont("Segoe UI Variable" if "Segoe UI Variable" in families else "Segoe UI")
        self.body_pixels = 17 if self.large else 14
        font.setPixelSize(self.body_pixels)
        self.setFont(font)
        self.items.setFont(font)
        self.items.header().setFont(font)
        self.tray.setFont(font)
        self.sidebar.setFixedWidth(223 if self.large else 207)
        self.inspector.setFixedWidth(273 if self.large else 255)
        self.setStyleSheet(stylesheet(self.colors))
        self.message.setFixedHeight(76 if self.large else 68)
        diagnostic_font = QFont("Cascadia Mono" if "Cascadia Mono" in families else "Consolas")
        diagnostic_font.setPixelSize(14 if self.large else 12)
        self.diagnostic_text.setFont(diagnostic_font)
        palette = self.message.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(self.colors["secondary"]))
        self.message.setPalette(palette)
        brand = glyph("brand", self.colors["primary"], 30)
        self.logo.setPixmap(brand)
        self.setWindowIcon(QIcon(brand))
        self.empty_image.setPixmap(glyph("usb", self.colors["primary"], 27))
        self.empty_image.setStyleSheet(
            f"background-color: {self.colors['selected']}; border-radius: 13px;"
        )
        for button in self.findChildren(QPushButton):
            key = button.property("iconKey")
            if key:
                color = self.colors["on_primary" if button is self.send else "secondary"]
                button.setIcon(icon(key, color, self.colors["off"]))

    def _retranslate(self):
        for button in self.findChildren(QPushButton):
            key = button.property("copyKey")
            if key:
                text = self.tr_key(key)
                button.setText("" if button is self.refresh else text)
                button.setAccessibleName(text)
                button.setToolTip(text)
        self.device_title.setText(self.tr_key("device"))
        self.devices.setAccessibleName(self.tr_key("device"))
        if self.devices.count() == 0:
            self.devices.addItem(self.tr_key("none"), None)
        else:
            self.devices.setItemText(0, self.tr_key("none"))
        self.empty_title.setText(self.tr_key("empty_title"))
        self.empty_body.setText(self.tr_key("empty_body"))
        self.compose_title.setText(self.tr_key("compose"))
        self.mode_label.setText(self.tr_key("mode"))
        self.mode.setAccessibleName(self.tr_key("mode"))
        self.message.setPlaceholderText(self.tr_key("placeholder"))
        self.message.setAccessibleName(self.tr_key("compose"))
        self.tray.setAccessibleName(self.tr_key("file"))
        self.items.setAccessibleName(self.tr_key("history"))
        self.detail_heading.setText(self.tr_key("details"))
        current_mode = self.mode.currentData()
        if current_mode is None:
            current_mode = Mode.AES256GCM
        self.mode.blockSignals(True)
        self.mode.clear()
        for mode in (Mode.AES256GCM, Mode.CHACHA20POLY1305, Mode.HMACSHA256, Mode.PLAINTEXT):
            self.mode.addItem(self.mode_name(mode), mode)
        self.mode.setCurrentIndex(self.mode.findData(current_mode))
        self.mode.blockSignals(False)
        self.items.setHeaderLabels([self.tr_key(key) for key in ("item", "mode", "status", "bytes")])
        self._view(self.show_history)
        for item, row in self.rows.values():
            self._paint_row(item, row)
        self._update_state()

    def _view(self, history):
        self.show_history = history
        self.conversation.setChecked(not history)
        self.history.setChecked(history)
        self.title.setText(self.tr_key("history" if history else "conversation"))
        self.subtitle.setText(self.tr_key("history_intro" if history else "title_intro"))
        self.compose.setVisible(not history)

    def _update_state(self):
        state = self.connection_state
        if state == "connected":
            tone, caption = "success", "connected"
        elif state in {"connecting", "pairing_code_connecting", "scanning", "paired"}:
            tone, caption = "primary", state
        elif state in {"disconnected", "select_device", "no_device"}:
            tone, caption = "secondary", "disconnected"
        elif state == "service_missing":
            tone, caption = "exposure", "setup_needed"
        else:
            tone, caption = "danger", "check_connection"
        self.connection_caption.setText(self.tr_key(caption))
        self.connection_caption.setStyleSheet(f"color: {self.colors[tone]}")
        show_help = state not in ACTIVE_CONNECTIONS | {"scanning", "paired", "disconnected"}
        self.connection_status.setText(self.tr_key(state) if show_help else "")
        self.connection_status.setVisible(show_help)
        self.diagnostic_text.setText(
            f"USBLinkLab {__version__}  /  {PROTOCOL_REVISION}\n"
            f"{self.tr_key('usb_only')}\n{self.connection_state}"
        )
        feedback_color = self.colors.get(self.feedback_type, self.colors["secondary"])
        self.feedback_label.setStyleSheet(f"color: {feedback_color}; font-size: {self.body_pixels}px")
        self.feedback_label.setText(self.tr_key(self.feedback))
        self.feedback_label.setVisible(bool(self.feedback))
        self._refresh_controls()
        self._show_detail()

    def _refresh_controls(self, *_):
        mode = self.mode.currentData()
        self.exposure.setText(self.mode_meaning(mode))
        self.exposure.setVisible(mode is not None)
        tone = "exposure" if mode in (0, 3) else "secondary"
        self.exposure.setStyleSheet(f"color: {self.colors[tone]}; font-size: {self.body_pixels}px")
        active = self.connection_state in ACTIVE_CONNECTIONS
        pairing = self.pairing_dialog is not None
        scanning = self.connection_state == "scanning"
        selected = self.devices.currentData() is not None
        self.devices.setEnabled(not (active or pairing or scanning) and self.devices.count() > 1)
        self.connect.setEnabled(not pairing and (active or (selected and not scanning)))
        connection_label = self.tr_key("disconnect" if active else "connect")
        self.connect.setText(connection_label)
        self.connect.setAccessibleName(connection_label)
        self.connect.setToolTip(connection_label)
        self.recipient.setText(self.devices.currentText() if selected else "")
        send_label = self.tr_key("send_now" if self.connection_state == "connected" else "send")
        self.send.setText(send_label)
        self.send.setAccessibleName(send_label)
        self.send.setToolTip(send_label)
        self.refresh.setEnabled(not (active or pairing or scanning))
        self.pair.setEnabled(selected and not (active or pairing or scanning))
        has_content = bool(self.attachments or self.message.toPlainText())
        self.send.setEnabled(selected and has_content and not self.staging)
        self.message.setReadOnly(self.staging)
        for widget in (self.mode, self.add_file, self.add_image, self.clear_files):
            widget.setEnabled(not self.staging)
        self.clear_files.setVisible(bool(self.attachments))
        self.compose_hint.setText(self.tr_key("staging" if self.staging else "choose_device"))
        self.compose_hint.setVisible(self.staging or (has_content and not selected))

    def _toggle_connection(self):
        if self.connection_state in ACTIVE_CONNECTIONS:
            self._cancel_session()
        elif self.devices.currentData() is not None:
            self.feedback = ""
            self._update_state()
            self.bridge.submit("connect", self.devices.currentData())

    def _confirm(self, title, body):
        return QMessageBox.question(self, self.tr_key(title), body,
                                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                    QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes

    def _cancel_session(self):
        if self._confirm("cancel", self.tr_key("cancel_explain")):
            self.bridge.submit("disconnect")

    def _choose_files(self, kind):
        if self.staging:
            return
        types = "Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.tif *.tiff *.heic);;All files (*)"
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr_key(kind), "", types if kind == "image" else "")
        self._add_attachments(paths, kind)

    def _add_attachments(self, paths, kind):
        if self.staging:
            return
        image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
        for value in paths:
            if len(self.attachments) == 32:
                self._update("error", "queue_full")
                break
            path = Path(value)
            try:
                size = path.stat().st_size
                if not path.is_file():
                    continue
            except OSError:
                continue
            if any(existing == path for existing, _ in self.attachments):
                continue
            actual_kind = ("image" if path.suffix.lower() in image_suffixes else "file") if kind == "auto" else kind
            self.attachments.append((path, actual_kind))
            self.tray.addTopLevelItem(QTreeWidgetItem([f"{path.name}    {byte_count(size)}"]))
        self.tray.setVisible(bool(self.attachments))
        self._refresh_controls()

    def _clear_files(self):
        self.attachments.clear()
        self.tray.clear()
        self.tray.hide()
        self._refresh_controls()

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.staging and not self.show_history and urls and all(url.isLocalFile() for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event):
        if not self.show_history:
            self._add_attachments([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()], "auto")
            event.acceptProposedAction()

    def _enqueue(self):
        if not self.send.isEnabled():
            return
        self.staging = True
        self.feedback = ""
        self._update_state()
        self.bridge.submit("enqueue", self.devices.currentData(), self.mode.currentData(),
                           self.message.toPlainText(), tuple(self.attachments))

    def _pair_dialog(self):
        device = self.devices.currentData()
        if device is None or not self.pair.isEnabled():
            return
        dialog = PairingDialog(self, device)
        self.pairing_dialog = dialog
        dialog.finished.connect(self._finish_pair_dialog)
        self._update_state()
        dialog.open()
        dialog.request_pairing()

    def _finish_pair_dialog(self, result):
        dialog = self.pairing_dialog
        self.pairing_dialog = None
        dialog.clear_pairing()
        dialog.deleteLater()
        if result == QDialog.DialogCode.Accepted:
            self.pairing_attempt = dialog.request_identity
            self.connection_state = "pairing_code_connecting"
            self.feedback = ""
            self._update_state()
            self.bridge.submit(f"pair_{dialog.method}", *dialog.request_identity)
        else:
            self.bridge.submit("cancel_pair_code", *dialog.request_identity)
            self._update_state()

    def closeEvent(self, event):
        if self.pairing_dialog is not None:
            self.pairing_dialog.reject()
        super().closeEvent(event)

    def _settings_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(self.tr_key("settings"))
        dialog.setMinimumWidth(410)
        body = QVBoxLayout(dialog)
        language = QComboBox()
        language.addItems(["한국어", "English"])
        language.setCurrentIndex(self.language)
        language.setAccessibleName("Language")
        theme = QComboBox()
        for key in ("dark", "light"):
            theme.addItem(self.tr_key(key), key)
        theme.setCurrentIndex(theme.findData(self.theme))
        theme.setAccessibleName("Theme")
        size = QComboBox()
        size.addItems([self.tr_key("normal"), self.tr_key("large")])
        size.setCurrentIndex(int(self.large))
        size.setAccessibleName("Text size")
        for widget in (language, theme, size):
            body.addWidget(widget)
        body.addWidget(label(self.tr_key("local"), "muted", wrap=True))
        save = QPushButton(self.tr_key("apply"), objectName="primary")
        save.clicked.connect(dialog.accept)
        body.addWidget(save)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.language = language.currentIndex()
            self.theme = theme.currentData()
            self.large = bool(size.currentIndex())
            self.preferences.setValue("language", self.language)
            self.preferences.setValue("theme", self.theme)
            self.preferences.setValue("large", self.large)
            self.preferences.sync()
            self._apply_appearance()
            self._retranslate()

    def _update(self, topic, value):
        if topic == "devices":
            selected = self.devices.currentData()
            self.devices.blockSignals(True)
            self.devices.clear()
            self.devices.addItem(self.tr_key("none"), None)
            for device, alias in value:
                self.devices.addItem(alias, device)
            index = self.devices.findData(selected)
            self.devices.setCurrentIndex(index if index >= 0 else 0)
            self.devices.blockSignals(False)
            if self.pairing_dialog is not None and self.devices.findData(self.pairing_dialog.device) < 0:
                self.pairing_dialog.reject()
        elif topic == "connection":
            self.connection_state = value
            if value != "pairing_code_connecting":
                self.pairing_attempt = None
        elif topic == "pairing_request_error":
            request = value["device"], value["request_id"]
            if self.pairing_dialog is not None and request == self.pairing_dialog.request_identity:
                self.pairing_dialog.show_unavailable(value["reason"])
            elif request == self.pairing_attempt:
                self.pairing_attempt = None
                self.connection_state = value["reason"]
        elif topic in {"pairing_qr", "pairing_code"}:
            if self.pairing_dialog is not None:
                self.pairing_dialog.show_pairing(topic, value)
        elif topic == "item":
            self._set_row(value)
        elif topic == "removed":
            if getattr(self, "_preview_key", None) and self._preview_key[0] == value:
                self._preview_key = None
                self._has_preview = False
                self.preview.clear()
            removed = self.rows.pop(value, None)
            if removed is not None:
                row, _ = removed
                self.items.takeTopLevelItem(self.items.indexOfTopLevelItem(row))
            self.items.setVisible(bool(self.rows))
            self.empty.setVisible(not self.rows)
        elif topic in {"error", "notice"}:
            self.feedback = value
            self.feedback_type = "danger" if topic == "error" else "success"
        elif topic == "enqueue_result":
            self.staging = False
            if value:
                self.message.clear()
                self._clear_files()
                self.mode.setCurrentIndex(self.mode.findData(Mode.AES256GCM))
        self._update_state()

    def _set_row(self, event):
        row_id = event["id"]
        existing = self.rows.get(row_id)
        if existing is None and event.get("previous_id"):
            existing = self.rows.pop(event["previous_id"], None)
            if existing is not None:
                existing[0].setData(0, Qt.ItemDataRole.UserRole, row_id)
        if existing is None:
            item = QTreeWidgetItem()
            item.setData(0, Qt.ItemDataRole.UserRole, row_id)
            self.items.addTopLevelItem(item)
        else:
            item = existing[0]
        self.rows[row_id] = (item, event)
        self._paint_row(item, event)
        self.items.show()
        self.empty.hide()

    def _paint_row(self, item, event):
        mode = event["mode"]
        text = event.get("text") if mode != Mode.PLAINTEXT else None
        name = event["name"] or (" ".join(text.split())[:108] if text else self.tr_key("compose"))
        direction = self.tr_key("out" if event["direction"] == "outgoing" else "in")
        item.setText(0, f"{direction}   {name}")
        item.setText(1, self.mode_name(mode))
        item.setToolTip(1, self.mode_meaning(mode))
        item.setText(2, self.tr_key(event["status"]))
        item.setText(3, byte_count(event["size"]) if event["status"] == "complete"
                     else f"{byte_count(event['transferred'])} / {byte_count(event['size'])}")
        item.setForeground(1, QColor(self.colors["exposure" if mode in (0, 3) else "primary"]))
        status_color = {
            "interrupted": "danger", "unconfirmed": "danger", "complete": "success",
        }.get(event["status"], "secondary")
        item.setForeground(2, QColor(self.colors[status_color]))

    def _selected(self):
        item = self.items.currentItem()
        row = self.rows.get(item.data(0, Qt.ItemDataRole.UserRole)) if item is not None else None
        return row[1] if row is not None else None

    def _toggle_details(self):
        if self.details_toggle.isChecked() and self.width() < 1030 and self.details_dialog is None:
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr_key("details"))
            layout = QVBoxLayout(dialog)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(self.inspector)
            self.details_dialog = dialog
            dialog.finished.connect(self._close_details_dialog)
            dialog.open()
        self.inspector.setVisible(self.details_toggle.isChecked())
        self._show_detail()

    def _close_details_dialog(self):
        dialog = self.details_dialog
        self.inspector.hide()
        self.root_layout.addWidget(self.inspector)
        self.details_dialog = None
        self.details_toggle.setChecked(False)
        dialog.deleteLater()

    def _open_details(self):
        self.details_toggle.setChecked(True)
        self._toggle_details()

    def _show_detail(self):
        if not self.details_toggle.isChecked():
            return
        event = self._selected()
        self.detail_name.setText((event["name"] or self.tr_key("compose")) if event else self.tr_key("details_empty"))
        self.detail_security.setVisible(event is not None)
        self.detail_status.setVisible(event is not None)
        self.progress.setVisible(event is not None and event["status"] in {"sending", "receiving", "verifying"})
        for widget in (self.preview, self.retry, self.remove, self.delete_message, self.export):
            widget.hide()
        self.cancel.setVisible(self.connection_state in ACTIVE_CONNECTIONS)
        if event is None:
            return
        status = event["status"]
        mode = event["mode"]
        self.detail_security.setText(f"{self.mode_name(mode)}\n{self.mode_meaning(mode)}")
        tone = "exposure" if mode in (0, 3) else "primary"
        self.detail_security.setStyleSheet(f"color: {self.colors[tone]}; font-size: {self.body_pixels}px")
        self.detail_status.setText(f"{self.tr_key(status)}  /  {byte_count(event['size'])}")
        self.progress.setValue(int(1000 * event["transferred"] / max(1, event["size"])))
        self.retry.setVisible(event["direction"] == "outgoing" and status in {"interrupted", "unconfirmed"})
        self.retry.setEnabled(self.devices.currentData() is not None and self.pairing_dialog is None and
                              self.connection_state not in ACTIVE_CONNECTIONS | {"scanning"})
        self.remove.setVisible(event["direction"] == "outgoing" and status in {
            "queued", "paused", "interrupted", "unconfirmed",
        })
        self.delete_message.setVisible(self._can_delete_message(event))
        self.export.setVisible(bool(event.get("path")))
        preview_key = (event["id"], status, event.get("text"))
        if getattr(self, "_preview_key", None) != preview_key:
            self._preview_key = preview_key
            self._render_preview(event)
        self.preview.setVisible(self._has_preview)

    def _render_preview(self, event):
        self._has_preview = False
        self.preview.clear()
        if event["status"] != "complete" and event["direction"] == "incoming":
            return
        if event.get("text"):
            self.preview.setText(event["text"][:480])
            self._has_preview = True
            return
        source = event.get("path")
        if not source or event["kind"] != "image" or event["size"] > 20 * 1024 * 1024:
            return
        reader = QImageReader(str(source))
        reader.setDecideFormatFromContent(True)
        reader.setAutoTransform(True)
        size = reader.size()
        if not size.isValid() or size.width() * size.height() > 20_000_000:
            return
        size.scale(QSize(221, 195), Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size)
        image = reader.read()
        if not image.isNull():
            self.preview.setPixmap(QPixmap.fromImage(image))
            self._has_preview = True

    def _retry_item(self):
        event = self._selected()
        if event is not None:
            text = f"{self.mode_name(event['mode'])}\n\n{self.tr_key('retry_explain')}"
            if self._confirm("retry", text):
                self.bridge.submit("retry", event["id"], self.devices.currentData())

    def _remove_item(self):
        event = self._selected()
        if event is not None:
            self.bridge.submit("remove", event["id"])

    @staticmethod
    def _can_delete_message(event):
        return (event is not None and event["direction"] == "incoming"
                and event["status"] == "complete" and event["kind"] == "text"
                and bool(event.get("path")))

    def _delete_message(self):
        event = self._selected()
        if self._can_delete_message(event) and self._confirm("delete_message", self.tr_key("delete_message_explain")):
            self.bridge.submit("delete_received_message", event["id"])

    def _export_item(self):
        event = self._selected()
        if event is None or not event["path"]:
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr_key("export"), event["name"] or "message.txt")
        if path:
            self.bridge.submit("export", event["path"], path)
