"""Draw the application icon in Qt, without external image assets."""

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap, QPolygonF


def icon_image(size=128) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.scale(size / 128, size / 128)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#b9adff"))
    painter.drawRoundedRect(4, 4, 120, 120, 30, 30)
    painter.setBrush(QColor("#282236"))
    painter.drawRoundedRect(26, 30, 76, 68, 12, 12)
    painter.setBrush(QColor("#ded7ff"))
    painter.drawPolygon(QPolygonF([QPointF(54, 46), QPointF(54, 82), QPointF(82, 64)]))
    painter.end()
    return image


def app_icon() -> QIcon:
    return QIcon(QPixmap.fromImage(icon_image()))
