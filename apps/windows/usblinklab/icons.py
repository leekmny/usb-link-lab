"""Original monoline icons for USBLinkLab's desktop controls."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


_STROKES = {
    "chat": (
        ((7, 10), (17, 10)), ((7, 14), (14, 14)),
        ((7, 18), (5, 21), (5, 17)),
    ),
    "list": (
        ((5, 6), (19, 6)), ((5, 12), (19, 12)), ((5, 18), (19, 18)),
    ),
    "refresh": (((20, 5), (20, 10), (15, 10)),),
    "pair": (((8, 13), (14, 7)), ((10, 17), (4, 17), (4, 11), (9, 6)),
             ((14, 7), (20, 7), (20, 13), (15, 18))),
    "file": (
        ((6, 3), (14, 3), (19, 8), (19, 21), (6, 21), (6, 3)),
        ((14, 3), (14, 8), (19, 8)),
    ),
    "image": (((5, 17), (10, 12), (14, 16), (17, 13), (20, 16)),),
    "details": (((15, 5), (15, 19)), ((18, 9), (19, 9)), ((18, 12), (19, 12))),
    "settings": (
        ((4, 6), (9, 6)), ((13, 6), (20, 6)), ((4, 12), (14, 12)),
        ((18, 12), (20, 12)), ((4, 18), (7, 18)), ((11, 18), (20, 18)),
    ),
    "diagnostics": (((7, 8), (11, 12), (7, 16)), ((13, 16), (17, 16))),
    "send": (((4, 5), (20, 12), (4, 19), (7, 12), (4, 5)), ((7, 12), (20, 12))),
    "usb": (
        ((12, 21), (12, 4)), ((9, 7), (12, 4), (15, 7)),
        ((12, 16), (7, 12), (7, 8)), ((12, 13), (17, 11), (17, 8)),
    ),
    "brand": (((7, 14), (10, 14), (14, 9), (17, 9)),),
    "chevron": (((6, 9), (12, 15), (18, 9)),),
}

_RECTANGLES = {
    "chat": (4, 5, 16, 13),
    "image": (4, 5, 16, 14),
    "details": (3, 5, 18, 14),
    "diagnostics": (3, 4, 18, 16),
    "brand": (3, 3, 18, 18),
}

_CIRCLES = {
    "image": ((9, 9, 1.3),),
    "settings": ((11, 6, 2), (16, 12, 2), (9, 18, 2)),
    "usb": ((7, 7, 1.2), (17, 7, 1.2), (12, 20, 1.4)),
}


def glyph(name, color, size):
    scale = 3
    pixmap = QPixmap(size * scale, size * scale)
    pixmap.setDevicePixelRatio(scale)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24, size / 24)
    pen = QPen(QColor(color), 1.65)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    if name in _RECTANGLES:
        painter.drawRoundedRect(QRectF(*_RECTANGLES[name]), 2.5, 2.5)
    for stroke in _STROKES.get(name, ()):
        path = QPainterPath()
        path.moveTo(*stroke[0])
        for point in stroke[1:]:
            path.lineTo(*point)
        painter.drawPath(path)
    for x, y, radius in _CIRCLES.get(name, ()):
        painter.drawEllipse(QRectF(x - radius, y - radius, 2 * radius, 2 * radius))
    if name == "refresh":
        painter.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 300 * 16)
    painter.end()
    return pixmap


def icon(name, color, disabled, size=18):
    result = QIcon()
    result.addPixmap(glyph(name, color, size))
    result.addPixmap(glyph(name, disabled, size), QIcon.Mode.Disabled)
    return result
