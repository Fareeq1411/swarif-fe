import json
import html
import ipaddress
import re
import shutil
import socket
import sys
import threading
from datetime import datetime

from pathlib import Path

from PyQt5.QtCore import QEvent, QMimeData, QPoint, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QDrag, QFont, QIcon, QPainter, QPainterPath, QPixmap, QRegion, QTransform
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
import qtawesome as qta

from agent import Agent
from error_logger import log


BLUE = "#2478EB"
DARK = "#15233A"
MUTED = "#74839A"
BORDER = "#DCE7F4"
PALE_BLUE = "#EAF3FF"
MAX_CONNECTION_LOG_BYTES = 1024 * 1024


def static_asset(name):
    """Return a development or PyInstaller-bundled static asset path."""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / "static" / name


class BotAvatar(QWidget):
    """Swarif's branded avatar for assistant messages and surfaces."""

    def __init__(self, size=38, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.logo = QPixmap(str(static_asset("Swarif_Logo.svg")))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#E4F0FF"))
        painter.drawEllipse(0, 0, side, side)
        if self.logo.isNull():
            return
        logo_side = max(1, int(side * 0.68))
        scaled = self.logo.scaled(
            QSize(logo_side, logo_side), Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        left = (self.width() - scaled.width()) // 2
        top = (self.height() - scaled.height()) // 2
        painter.drawPixmap(left, top, scaled)


class UserAvatar(QWidget):
    """Generic user display picture shown beside the signed-in user's name."""

    def __init__(self, size=34, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.icon = qta.icon("fa5s.user", color="#2478EB")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#DCEBFD"))
        painter.drawEllipse(0, 0, side, side)
        margin = max(6, int(side * 0.25))
        self.icon.paint(
            painter,
            margin,
            margin,
            max(1, self.width() - margin * 2),
            max(1, self.height() - margin * 2),
        )


class BrandMark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)
        self.logo = QPixmap(str(static_asset("Swarif_Logo.svg")))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.logo.isNull():
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#F3F7FC"))
            painter.drawEllipse(1, 1, self.width() - 2, self.height() - 2)
            scaled = self.logo.scaled(
                QSize(26, 26), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            left = (self.width() - scaled.width()) // 2
            top = (self.height() - scaled.height()) // 2
            painter.drawPixmap(left, top, scaled)


class TitleBar(QFrame):
    minimize_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag_offset = None
        self.setObjectName("titleBar")
        self.setFixedHeight(68)

        layout = QHBoxLayout(self)
        self.content_layout = layout
        layout.setContentsMargins(21, 0, 14, 0)
        layout.setSpacing(8)

        self.mark = BrandMark()
        layout.addWidget(self.mark)
        self.brand = QLabel("Swarif")
        self.brand.setObjectName("brand")
        layout.addWidget(self.brand)
        layout.addStretch()

        self.minimize = QPushButton()
        self.minimize.setObjectName("windowButton")
        self.minimize.setIcon(qta.icon("fa5s.window-minimize", color="#526176"))
        self.minimize.setIconSize(QSize(13, 13))
        self.minimize.setToolTip("Minimize to floating icon")
        self.minimize.clicked.connect(self.minimize_clicked)
        self.close_button = QPushButton()
        self.close_button.setObjectName("windowButton")
        self.close_button.setIcon(qta.icon("fa5s.times", color="#526176"))
        self.close_button.setIconSize(QSize(15, 15))
        self.close_button.setToolTip("Close")
        self.close_button.clicked.connect(self.close_clicked)
        layout.addWidget(self.minimize)
        layout.addWidget(self.close_button)

    def set_compact(self, compact):
        if compact:
            self.content_layout.setContentsMargins(14, 0, 14, 0)
        else:
            self.content_layout.setContentsMargins(21, 0, 14, 0)
        self.mark.setVisible(True)
        self.brand.setVisible(not compact)
        self.close_button.setVisible(True)
        self.minimize.setFixedSize(30, 30)
        icon_name = "fa5s.expand-alt" if compact else "fa5s.window-minimize"
        self.minimize.setIcon(qta.icon(icon_name, color="#526176"))
        self.minimize.setToolTip("Restore Swarif" if compact else "Minimize to floating icon")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPos() - self.window().frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.window().move(event.globalPos() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = None
        super().mouseReleaseEvent(event)

def set_selected_elevation(widget, selected):
    """Apply a soft raised shadow to a selected blue control."""
    if not selected:
        widget.setGraphicsEffect(None)
        return
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(18)
    shadow.setOffset(0, 5)
    shadow.setColor(QColor(24, 104, 213, 90))
    widget.setGraphicsEffect(shadow)


class NavButton(QPushButton):
    def __init__(self, icon_name, text, active=False, parent=None):
        super().__init__(text, parent)
        self.icon_name = icon_name
        self.setIconSize(QSize(17, 17))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(46)
        self.set_active(active)

    def set_active(self, active):
        self.setObjectName("navActive" if active else "navButton")
        self.setIcon(
            qta.icon(self.icon_name, color="white" if active else "#40516A")
        )
        set_selected_elevation(self, active)
        self.style().unpolish(self)
        self.style().polish(self)


class ToggleSwitch(QCheckBox):
    """A compact labelled on/off switch with a visible sliding knob."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.setMinimumWidth(132)
        self.stateChanged.connect(self.update)

    def sizeHint(self):
        return QSize(132, 30)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        track = QRectF(1, 5, 40, 20)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#32AE77" if self.isChecked() else "#CBD5E1"))
        painter.drawRoundedRect(track, 10, 10)

        knob_x = 22 if self.isChecked() else 3
        painter.setBrush(QColor("white"))
        painter.drawEllipse(QRectF(knob_x, 7, 16, 16))

        painter.setPen(QColor("#176443" if self.isChecked() else "#526176"))
        painter.setFont(self.font())
        painter.drawText(
            QRectF(49, 0, max(0, self.width() - 49), self.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self.text(),
        )


class Sidebar(QFrame):
    logout_clicked = pyqtSignal()
    chat_clicked = pyqtSignal()
    jobs_clicked = pyqtSignal()
    files_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()

    def __init__(self, user_name="Swarif", active_page="chat", connected=False, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(190)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 18, 13, 17)
        layout.setSpacing(9)

        self.nav_buttons = {
            "chat": NavButton("fa5s.comment-alt", "Chat", active=active_page == "chat"),
            "jobs": NavButton("fa5s.briefcase", "Jobs", active=active_page == "jobs"),
            "files": NavButton("fa5s.folder-open", "Files", active=active_page == "files"),
        }
        self.nav_buttons["chat"].clicked.connect(
            lambda: self.activate_and_emit("chat", self.chat_clicked)
        )
        self.nav_buttons["jobs"].clicked.connect(
            lambda: self.activate_and_emit("jobs", self.jobs_clicked)
        )
        self.nav_buttons["files"].clicked.connect(
            lambda: self.activate_and_emit("files", self.files_clicked)
        )
        for button in self.nav_buttons.values():
            layout.addWidget(button)
        layout.addStretch()

        profile = QFrame()
        profile.setObjectName("profile")
        profile_layout = QHBoxLayout(profile)
        profile_layout.setContentsMargins(5, 8, 3, 8)
        profile_layout.setSpacing(8)
        profile_layout.addWidget(UserAvatar(34))

        profile_copy = QVBoxLayout()
        profile_copy.setSpacing(0)
        name = QLabel(user_name)
        name.setObjectName("profileName")
        self.status = QLabel()
        self.status.setObjectName("status")
        profile_copy.addWidget(name)
        profile_copy.addWidget(self.status)
        profile_layout.addLayout(profile_copy)
        layout.addWidget(profile)

        logout = NavButton("fa5s.sign-out-alt", "Log out")
        logout.setObjectName("logoutButton")
        logout.clicked.connect(self.logout_clicked)
        settings_button = QPushButton()
        settings_button.setObjectName(
            "settingsIconActive" if active_page == "settings" else "settingsIconButton"
        )
        settings_button.setIcon(
            qta.icon(
                "fa5s.cog",
                color="white" if active_page == "settings" else "#40516A",
            )
        )
        settings_button.setIconSize(QSize(17, 17))
        settings_button.setFixedSize(46, 46)
        settings_button.setCursor(Qt.PointingHandCursor)
        settings_button.setToolTip("Settings")
        settings_button.setAccessibleName("Settings")
        set_selected_elevation(settings_button, active_page == "settings")
        settings_button.clicked.connect(
            lambda: self.activate_and_emit("settings", self.settings_clicked)
        )
        self.settings_button = settings_button

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        actions.addWidget(logout, 1)
        actions.addWidget(settings_button)
        layout.addLayout(actions)
        self.set_connected(connected)

    def activate_and_emit(self, page, signal):
        for name, button in self.nav_buttons.items():
            button.set_active(name == page)
        settings_active = page == "settings"
        self.settings_button.setObjectName(
            "settingsIconActive" if settings_active else "settingsIconButton"
        )
        self.settings_button.setIcon(
            qta.icon("fa5s.cog", color="white" if settings_active else "#40516A")
        )
        set_selected_elevation(self.settings_button, settings_active)
        self.settings_button.style().unpolish(self.settings_button)
        self.settings_button.style().polish(self.settings_button)
        signal.emit()

    def set_connected(self, connected):
        self.status.setText("●  Connected" if connected else "●  Disconnected")
        self.status.setProperty("connected", bool(connected))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


def format_chat_message(value, outgoing=False):
    """Render a small, HTML-safe Markdown subset for chat bubbles."""
    text = value if isinstance(value, str) else str(value or "")
    foreground = "#FFFFFF" if outgoing else "#15233A"
    code_background = "#1768D5" if outgoing else "#DCEBFD"
    link_color = "#FFFFFF" if outgoing else "#1768D5"
    token_pattern = re.compile(
        r"(`[^`\n]+`|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\(https?://[^)\s]+\))"
    )

    def inline(source):
        rendered = []
        cursor = 0
        for match in token_pattern.finditer(source):
            rendered.append(html.escape(source[cursor:match.start()]))
            token = match.group(0)
            if token.startswith("`"):
                code = html.escape(token[1:-1])
                rendered.append(
                    f'<span style="font-family: monospace; background-color: {code_background}; '
                    f'color: {foreground};">&nbsp;{code}&nbsp;</span>'
                )
            elif token.startswith("**"):
                rendered.append(f"<b>{html.escape(token[2:-2])}</b>")
            else:
                label, url = re.match(r"\[([^]]+)\]\((https?://[^)]+)\)", token).groups()
                rendered.append(
                    f'<a href="{html.escape(url, quote=True)}" '
                    f'style="color: {link_color}; text-decoration: underline;">'
                    f"{html.escape(label)}</a>"
                )
            cursor = match.end()
        rendered.append(html.escape(source[cursor:]))
        return "".join(rendered)

    rendered_lines = []
    for line in text.splitlines():
        heading = re.fullmatch(r"\s*\*\*(.+?)\*\*\s*", line)
        if heading:
            rendered_lines.append(
                f'<div style="font-size: 15px; font-weight: 700; color: {foreground}; '
                f'margin-top: 7px; margin-bottom: 2px;">{html.escape(heading.group(1))}</div>'
            )
        elif re.match(r"^\s*[-•]\s+", line):
            content = re.sub(r"^\s*[-•]\s+", "", line)
            rendered_lines.append(f'<div style="margin-left: 6px;">&#8226;&nbsp;&nbsp;{inline(content)}</div>')
        elif line.strip():
            rendered_lines.append(f"<div>{inline(line)}</div>")
        else:
            rendered_lines.append("<br>")
    return "".join(rendered_lines)


class MessageBubble(QWidget):
    def __init__(self, text, timestamp, outgoing=False, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 12)
        outer.setSpacing(4)

        line = QHBoxLayout()
        line.setSpacing(8)
        if outgoing:
            line.addStretch()
        else:
            line.addWidget(BotAvatar(32), 0, Qt.AlignTop)

        bubble = QLabel(format_chat_message(text, outgoing=outgoing))
        bubble.setTextFormat(Qt.RichText)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse
        )
        bubble.setOpenExternalLinks(True)
        bubble.setObjectName("bubbleOutgoing" if outgoing else "bubbleIncoming")
        bubble.setMaximumWidth(255)
        bubble.setMinimumWidth(235 if outgoing else 220)
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        bubble_shadow = QGraphicsDropShadowEffect(bubble)
        bubble_shadow.setBlurRadius(18)
        bubble_shadow.setOffset(0, 5)
        bubble_shadow.setColor(QColor(31, 77, 133, 52))
        bubble.setGraphicsEffect(bubble_shadow)
        line.addWidget(bubble, 0, Qt.AlignTop)
        if not outgoing:
            line.addStretch()
        outer.addLayout(line)

        time_line = QHBoxLayout()
        if outgoing:
            time_line.addStretch()
            stamp = QLabel(f"{timestamp}  ✓✓")
        else:
            time_line.addSpacing(40)
            stamp = QLabel(timestamp)
            time_line.addStretch()
        stamp.setObjectName("timestamp")
        time_line.insertWidget(1 if outgoing else 0, stamp)
        outer.addLayout(time_line)


class AnimatedDots(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("typingDots")
        self.setFixedWidth(24)
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.advance)
        self.advance()
        self._timer.start()

    def advance(self):
        self._frame = (self._frame % 3) + 1
        self.setText("." * self._frame)


class TypingIndicator(QWidget):
    def __init__(self, summary="Thinking", parent=None):
        super().__init__(parent)
        self.setObjectName("typingIndicator")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(8)
        layout.addWidget(BotAvatar(32), 0, Qt.AlignTop)
        bubble = QFrame()
        bubble.setObjectName("typingBubble")
        bubble_layout = QHBoxLayout(bubble)
        bubble_layout.setContentsMargins(13, 7, 13, 9)
        self.dots = AnimatedDots()
        bubble_layout.addWidget(self.dots)
        self.summary = QLabel()
        self.summary.setObjectName("typingSummary")
        self.summary.setWordWrap(False)
        bubble_layout.addWidget(self.summary)
        self.set_summary(summary)
        layout.addWidget(bubble, 0, Qt.AlignTop)
        layout.addStretch()

    def set_summary(self, summary):
        text = summary.strip() if isinstance(summary, str) else ""
        self.summary.setText(text or "Thinking")


def format_chat_time(value):
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone().strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return value


def is_local_agent_ip(agent_ip):
    """Return whether an IPv4 address belongs to this machine."""
    try:
        address = ipaddress.IPv4Address(agent_ip)
    except ipaddress.AddressValueError:
        return False
    if address.is_loopback:
        return True

    local_addresses = set()
    try:
        for result in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM
        ):
            local_addresses.add(result[4][0])
    except socket.gaierror:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((str(address), 9))
            local_addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return str(address) in local_addresses


def human_file_size(size):
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


class FileTree(QTreeWidget):
    files_dropped = pyqtSignal(list)
    SWARIF_DRAG_FORMAT = "application/x-swarif-copy-source"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("fileTree")
        self.setColumnCount(4)
        self.setHeaderLabels(["Name", "Date modified", "Type", "Size"])
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeWidget.DragDrop)
        self.setDefaultDropAction(Qt.CopyAction)
        self.setDropIndicatorShown(True)
        self.setColumnWidth(0, 265)
        self.setColumnWidth(1, 145)
        self.setColumnWidth(2, 90)
        self.setColumnWidth(3, 75)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(self.SWARIF_DRAG_FORMAT):
            event.ignore()
            return
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(self.SWARIF_DRAG_FORMAT):
            event.ignore()
            return
        if event.mimeData().hasUrls():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        event.ignore()

    def dropEvent(self, event):
        if event.mimeData().hasFormat(self.SWARIF_DRAG_FORMAT):
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        event.ignore()

    def startDrag(self, _supported_actions):
        paths = []
        for item in self.selectedItems():
            value = item.data(0, Qt.UserRole)
            if value and Path(value).exists():
                paths.append(Path(value))
        if not paths:
            return

        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
        mime_data.setData(self.SWARIF_DRAG_FORMAT, b"copy-only")
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.setPixmap(self.icon_provider_pixmap(paths[0]))
        drag.exec_(Qt.CopyAction, Qt.CopyAction)

    @staticmethod
    def icon_provider_pixmap(path):
        provider = QFileIconProvider()
        icon_type = QFileIconProvider.Folder if path.is_dir() else QFileIconProvider.File
        return provider.icon(icon_type).pixmap(32, 32)


class FilesPanel(QFrame):
    """An Explorer-style view that only copies imports into a user's workspace."""

    def __init__(self, root_path, parent=None):
        super().__init__(parent)
        self.setObjectName("filesPanel")
        self.root_path = Path(root_path)
        self.current_path = self.root_path
        self.icon_provider = QFileIconProvider()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(12)

        heading = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(3)
        title = QLabel("Files")
        title.setObjectName("sectionTitle")
        subtitle = QLabel("Files available to Swarif. Imports are always copied; originals stay in place.")
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        copy.addWidget(title)
        copy.addWidget(subtitle)
        heading.addLayout(copy, 1)

        self.add_button = QPushButton("  Add files")
        self.add_button.setObjectName("filesPrimary")
        self.add_button.setIcon(qta.icon("fa5s.plus", color="white"))
        self.add_button.clicked.connect(self.choose_files)
        folder_button = QPushButton("  New folder")
        folder_button.setObjectName("filesSecondary")
        folder_button.setIcon(qta.icon("fa5s.folder-plus", color=BLUE))
        folder_button.clicked.connect(self.create_folder)
        heading.addWidget(folder_button)
        heading.addWidget(self.add_button)
        layout.addLayout(heading)

        navigation = QHBoxLayout()
        self.back_button = QPushButton()
        self.back_button.setObjectName("filesBack")
        self.back_button.setIcon(qta.icon("fa5s.arrow-left", color="#526176"))
        self.back_button.setToolTip("Back to parent folder")
        self.back_button.clicked.connect(self.go_back)
        self.location = QLabel()
        self.location.setObjectName("filesLocation")
        navigation.addWidget(self.back_button)
        navigation.addWidget(self.location, 1)
        layout.addLayout(navigation)

        self.tree = FileTree()
        self.tree.itemDoubleClicked.connect(self.open_item)
        self.tree.files_dropped.connect(self.copy_paths)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.show_context_menu)
        layout.addWidget(self.tree, 1)

        self.empty = QLabel("Drop files here to copy them into this folder")
        self.empty.setObjectName("filesHint")
        self.empty.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.empty)
        self.refresh()

    def safe_child(self, path):
        try:
            Path(path).resolve(strict=False).relative_to(self.root_path.resolve(strict=False))
            return True
        except ValueError:
            return False

    def refresh(self):
        self.tree.clear()
        try:
            if not self.root_path.exists():
                self.root_path.mkdir(parents=True, exist_ok=True)
            entries = sorted(
                self.current_path.iterdir(),
                key=lambda entry: (not entry.is_dir(), entry.name.casefold()),
            )
        except OSError as error:
            self.empty.setText(f"Files folder is unavailable: {error}")
            self.empty.show()
            self.add_button.setEnabled(False)
            return

        self.add_button.setEnabled(True)
        relative = self.current_path.relative_to(self.root_path)
        self.location.setText("Swarif Files" + (f" / {relative.as_posix()}" if relative.parts else ""))
        self.back_button.setEnabled(self.current_path != self.root_path)
        for path in entries:
            try:
                stat = path.stat()
                is_directory = path.is_dir()
            except OSError:
                continue
            item = QTreeWidgetItem([
                path.name,
                datetime.fromtimestamp(stat.st_mtime).strftime("%d %b %Y, %I:%M %p"),
                "File folder" if is_directory else (path.suffix[1:].upper() + " file" if path.suffix else "File"),
                "" if is_directory else human_file_size(stat.st_size),
            ])
            item.setData(0, Qt.UserRole, str(path))
            item.setIcon(0, self.icon_provider.icon(QFileIconProvider.Folder if is_directory else QFileIconProvider.File))
            self.tree.addTopLevelItem(item)
        self.empty.setText("Drop files here to copy them into this folder")
        self.empty.setVisible(not entries)

    def open_item(self, item, _column):
        path = Path(item.data(0, Qt.UserRole))
        if not self.safe_child(path):
            return
        if path.is_dir():
            self.current_path = path
            self.refresh()
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "Unable to open file", f"No application could open {path.name}.")

    def selected_paths(self):
        paths = [Path(item.data(0, Qt.UserRole)) for item in self.tree.selectedItems()]
        return [path for path in paths if self.safe_child(path) and path != self.root_path]

    def show_context_menu(self, position):
        clicked = self.tree.itemAt(position)
        if clicked is not None and not clicked.isSelected():
            self.tree.clearSelection()
            clicked.setSelected(True)
            self.tree.setCurrentItem(clicked)
        paths = self.selected_paths()

        menu = QMenu(self)
        open_action = menu.addAction("Open")
        open_action.setEnabled(len(paths) == 1)
        rename_action = menu.addAction("Rename")
        rename_action.setEnabled(len(paths) == 1)
        duplicate_action = menu.addAction("Duplicate")
        duplicate_action.setEnabled(bool(paths))
        menu.addSeparator()
        copy_path_action = menu.addAction("Copy path")
        copy_path_action.setEnabled(bool(paths))
        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(bool(paths))
        menu.addSeparator()
        new_folder_action = menu.addAction("New folder")
        refresh_action = menu.addAction("Refresh")

        chosen = menu.exec_(self.tree.viewport().mapToGlobal(position))
        if chosen == open_action and paths:
            self.open_path(paths[0])
        elif chosen == rename_action and paths:
            self.rename_path(paths[0])
        elif chosen == duplicate_action:
            self.copy_paths([str(path) for path in paths])
        elif chosen == copy_path_action:
            QApplication.clipboard().setText("\n".join(str(path) for path in paths))
        elif chosen == delete_action:
            self.delete_paths(paths)
        elif chosen == new_folder_action:
            self.create_folder()
        elif chosen == refresh_action:
            self.refresh()

    def open_path(self, path):
        if not self.safe_child(path) or not path.exists():
            return
        if path.is_dir():
            self.current_path = path
            self.refresh()
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(self, "Unable to open file", f"No application could open {path.name}.")

    def rename_path(self, path):
        name, accepted = QInputDialog.getText(
            self, "Rename", "New name:", QLineEdit.Normal, path.name
        )
        name = name.strip()
        if not accepted or not name or name == path.name:
            return
        if not self.valid_name(name):
            QMessageBox.warning(self, "Invalid name", "Enter a name without path separators.")
            return
        destination = path.with_name(name)
        if destination.exists():
            QMessageBox.warning(self, "Name already exists", f'An item named "{name}" already exists.')
            return
        try:
            path.rename(destination)
        except OSError as error:
            log(error)
            QMessageBox.warning(self, "Unable to rename item", str(error))
            return
        self.refresh()

    def delete_paths(self, paths):
        if not paths:
            return
        label = paths[0].name if len(paths) == 1 else f"{len(paths)} selected items"
        answer = QMessageBox.warning(
            self,
            "Delete permanently?",
            f'Delete {label}?\n\nThis removes it from the Swarif workspace and cannot be undone.',
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        failures = []
        for path in paths:
            if not self.safe_child(path) or path == self.root_path:
                failures.append(path.name)
                continue
            try:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            except OSError as error:
                log(error)
                failures.append(path.name)
        self.refresh()
        if failures:
            QMessageBox.warning(self, "Some items were not deleted", "Unable to delete: " + ", ".join(failures))

    def go_back(self):
        if self.current_path != self.root_path:
            self.current_path = self.current_path.parent
            self.refresh()

    def choose_files(self):
        paths, _selected_filter = QFileDialog.getOpenFileNames(
            self,
            "Copy files into Swarif",
            options=QFileDialog.DontUseNativeDialog,
        )
        if paths:
            self.copy_paths(paths)

    def available_destination(self, name):
        destination = self.current_path / name
        if not destination.exists():
            return destination
        source_name = Path(name)
        stem = source_name.stem
        suffix = source_name.suffix
        number = 1
        while True:
            label = f"{stem} copy{f' {number}' if number > 1 else ''}{suffix}"
            destination = self.current_path / label
            if not destination.exists():
                return destination
            number += 1

    def copy_paths(self, paths):
        failures = []
        copied = 0
        for value in paths:
            source = Path(value)
            if not source.exists():
                failures.append(source.name or str(source))
                continue
            destination = self.available_destination(source.name)
            try:
                destination.resolve(strict=False).relative_to(source.resolve(strict=False))
                failures.append(source.name)
                continue
            except ValueError:
                pass
            try:
                if source.is_dir() and not source.is_symlink():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
                copied += 1
            except OSError as error:
                log(error)
                failures.append(source.name)
        self.refresh()
        if failures:
            QMessageBox.warning(
                self,
                "Some files were not copied",
                "Unable to copy: " + ", ".join(failures),
            )
        elif copied:
            self.empty.setText(f"Copied {copied} item{'s' if copied != 1 else ''}.")

    def create_folder(self):
        name, accepted = QInputDialog.getText(self, "New folder", "Folder name:")
        name = name.strip()
        if not accepted or not name:
            return
        if not self.valid_name(name):
            QMessageBox.warning(self, "Invalid folder name", "Enter a folder name without path separators.")
            return
        destination = self.current_path / name
        try:
            destination.mkdir()
        except FileExistsError:
            QMessageBox.warning(self, "Folder already exists", f'A folder named "{name}" already exists.')
            return
        except OSError as error:
            log(error)
            QMessageBox.warning(self, "Unable to create folder", str(error))
            return
        self.refresh()

    @staticmethod
    def valid_name(name):
        return bool(name) and name not in {".", ".."} and Path(name).name == name and "/" not in name and "\\" not in name


class GrowingMessageEdit(QTextEdit):
    submit_requested = pyqtSignal()
    height_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setLineWrapMode(QTextEdit.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.document().documentLayout().documentSizeChanged.connect(
            self.update_editor_height
        )
        self.textChanged.connect(self.update_editor_height)
        self.update_editor_height()

    def update_editor_height(self, *_args):
        document_height = self.document().documentLayout().documentSize().height()
        margins = self.contentsMargins()
        target = int(document_height + margins.top() + margins.bottom() + 10)
        target = max(34, min(target, 180))
        changed = self.height() != target
        self.setFixedHeight(target)
        if changed:
            self.height_changed.emit(target)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (
            event.modifiers() & Qt.ShiftModifier
        ):
            self.submit_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Composer(QFrame):
    message_submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("composer")
        self.setMinimumHeight(58)
        composer_shadow = QGraphicsDropShadowEffect(self)
        composer_shadow.setBlurRadius(28)
        composer_shadow.setOffset(0, 5)
        composer_shadow.setColor(QColor(36, 120, 235, 55))
        self.setGraphicsEffect(composer_shadow)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 7, 8, 7)
        layout.setSpacing(8)

        attach = QPushButton()
        attach.setObjectName("attachButton")
        attach.setIcon(qta.icon("fa5s.paperclip", color=BLUE))
        attach.setIconSize(QSize(16, 16))
        attach.setToolTip("Attach a file")
        self.message = GrowingMessageEdit()
        self.message.setObjectName("messageInput")
        self.message.setPlaceholderText("Type a message...")
        self.message.submit_requested.connect(self.submit_message)
        self.send = QPushButton()
        self.send.setObjectName("sendButton")
        self.send.setIcon(qta.icon("fa5s.paper-plane", color="white"))
        self.send.setIconSize(QSize(18, 18))
        self.send.setToolTip("Send (Shift+Enter)")
        self.send.setAccessibleName("Send message")
        self.send.clicked.connect(self._button_clicked)
        send_shadow = QGraphicsDropShadowEffect(self.send)
        send_shadow.setBlurRadius(16)
        send_shadow.setOffset(0, 4)
        send_shadow.setColor(QColor(16, 111, 232, 95))
        self.send.setGraphicsEffect(send_shadow)

        self.stop_status = QLabel("Stopping…")
        self.stop_status.setObjectName("stopStatus")
        self.stop_status.setAlignment(Qt.AlignCenter)
        self.stop_status.hide()

        send_layout = QVBoxLayout()
        send_layout.setContentsMargins(0, 0, 0, 0)
        send_layout.setSpacing(2)
        send_layout.addWidget(self.stop_status)
        send_layout.addWidget(self.send, 0, Qt.AlignHCenter)

        layout.addWidget(attach)
        layout.addWidget(self.message, 1)
        layout.addLayout(send_layout)

        self._task_state = "idle"

    def set_task_state(self, state):
        self._task_state = state if state in {
            "idle", "thinking", "executing", "stopping"
        } else "idle"
        log(f"Stop button state: {self._task_state}")
        stopping = self._task_state == "stopping"
        active = self._task_state in {"thinking", "executing"}
        self.message.setEnabled(not active and not stopping)
        self.send.setEnabled(not stopping)
        self.stop_status.setVisible(stopping)
        if stopping:
            self.send.setText("")
            self.send.setIcon(qta.icon("fa5s.stop", color="#DCE8F8"))
            self.send.setToolTip("Stopping…")
            self.send.setAccessibleName("Stopping task")
        elif active:
            self.send.setText("")
            self.send.setIcon(qta.icon("fa5s.stop", color="white"))
            self.send.setToolTip("Stop")
            self.send.setAccessibleName("Stop task")
        else:
            self.send.setText("")
            self.send.setIcon(qta.icon("fa5s.paper-plane", color="white"))
            self.send.setToolTip("Send (Shift+Enter)")
            self.send.setAccessibleName("Send message")

    def _button_clicked(self):
        if self._task_state in {"thinking", "executing"}:
            self.set_task_state("stopping")
            self.stop_requested.emit()
        elif self._task_state == "idle":
            self.submit_message()

    def submit_message(self):
        message = self.message.toPlainText().strip()
        if not message:
            return
        self.message.clear()
        self.message_submitted.emit(message)


class LoginPage(QFrame):
    login_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loginPage")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(52, 30, 52, 45)
        outer.addStretch()

        form = QFrame()
        form.setObjectName("loginCard")
        form.setMaximumWidth(360)
        form_layout = QVBoxLayout(form)
        form_layout.setContentsMargins(32, 30, 32, 30)
        form_layout.setSpacing(10)

        avatar_row = QHBoxLayout()
        avatar_row.addStretch()
        avatar_row.addWidget(BotAvatar(62))
        avatar_row.addStretch()
        form_layout.addLayout(avatar_row)

        title = QLabel("Welcome to Swarif")
        title.setObjectName("loginTitle")
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel("Sign in to continue to your AI front agent")
        subtitle.setObjectName("loginSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        form_layout.addWidget(title)
        form_layout.addWidget(subtitle)
        form_layout.addSpacing(12)

        email_label = QLabel("Email address")
        email_label.setObjectName("fieldLabel")
        self.email = QLineEdit()
        self.email.setObjectName("loginInput")
        self.email.setPlaceholderText("you@example.com")
        self.email.addAction(
            qta.icon("fa5s.envelope", color="#8796AA"),
            QLineEdit.LeadingPosition,
        )
        self.email.setClearButtonEnabled(True)

        password_label = QLabel("Password")
        password_label.setObjectName("fieldLabel")
        self.password = QLineEdit()
        self.password.setObjectName("loginInput")
        self.password.setPlaceholderText("Enter your password")
        self.password.setEchoMode(QLineEdit.Password)
        self.password.addAction(
            qta.icon("fa5s.lock", color="#8796AA"),
            QLineEdit.LeadingPosition,
        )
        self.password.returnPressed.connect(self.login_requested)

        form_layout.addWidget(email_label)
        form_layout.addWidget(self.email)
        form_layout.addSpacing(4)
        form_layout.addWidget(password_label)
        form_layout.addWidget(self.password)

        options = QHBoxLayout()
        self.remember = QCheckBox("Remember me")
        self.remember.setObjectName("rememberCheck")
        forgot = QPushButton("Forgot password?")
        forgot.setObjectName("textButton")
        options.addWidget(self.remember)
        options.addStretch()
        options.addWidget(forgot)
        form_layout.addLayout(options)

        self.error = QLabel("")
        self.error.setObjectName("loginError")
        self.error.setAlignment(Qt.AlignCenter)
        self.error.setWordWrap(True)
        self.error.hide()
        form_layout.addWidget(self.error)

        login = QPushButton("Sign in")
        login.setObjectName("loginButton")
        login.setIcon(qta.icon("fa5s.arrow-right", color="white"))
        login.setIconSize(QSize(15, 15))
        login.setCursor(Qt.PointingHandCursor)
        login.clicked.connect(self.login_requested)
        form_layout.addWidget(login)

        secure = QLabel("●  Secure local session")
        secure.setObjectName("secureLabel")
        secure.setAlignment(Qt.AlignCenter)
        form_layout.addWidget(secure)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(form)
        row.addStretch()
        outer.addLayout(row)
        outer.addStretch()

    def show_error(self, message):
        self.error.setText(message)
        self.error.show()


def display_job_value(value):
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, indent=2, ensure_ascii=False)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith(("{", "[")):
            try:
                return json.dumps(json.loads(stripped), indent=2, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        return value
    return str(value)


def format_task_objectives(task):
    """Turn a structured task payload into readable objective text."""
    if isinstance(task, str):
        stripped = task.strip()
        if stripped.startswith(("{", "[")):
            try:
                return format_task_objectives(json.loads(stripped))
            except json.JSONDecodeError:
                pass
        return stripped or "—"

    if isinstance(task, dict):
        for key in ("instructions", "objectives", "objective", "prompt", "task"):
            if key in task and task[key] not in (None, "", [], {}):
                return format_task_objectives(task[key])

        lines = []
        for key, value in task.items():
            if value in (None, "", [], {}):
                continue
            label = key.replace("_", " ").strip().capitalize()
            formatted = format_task_objectives(value)
            if "\n" in formatted:
                lines.append(f"{label}:\n{formatted}")
            else:
                lines.append(f"{label}: {formatted}")
        return "\n".join(lines) or "—"

    if isinstance(task, list):
        lines = []
        for item in task:
            formatted = format_task_objectives(item)
            formatted_lines = formatted.splitlines() or ["—"]
            lines.append(f"• {formatted_lines[0]}")
            lines.extend(f"  {line}" for line in formatted_lines[1:])
        return "\n".join(lines) or "—"

    return str(task) if task is not None else "—"


def job_status_text(job, job_type):
    try:
        status = int(job.get("status"))
    except (TypeError, ValueError):
        status = None
    if job_type == "active":
        return {0: "Pending", 3: "Processing"}.get(status, "Active")
    return {1: "Success", 2: "Failed"}.get(status, "Inactive")


def job_is_processing(job):
    try:
        return int(job.get("status")) == 3
    except (TypeError, ValueError, AttributeError):
        return False


def partition_jobs_by_status(*job_collections):
    """Put statuses 0/3 in Active and statuses 1/2 in Inactive."""
    merged_jobs = []
    positions = {}
    for collection in job_collections:
        for job in collection or []:
            if not isinstance(job, dict):
                continue
            identity = job.get("id")
            if identity is not None and identity in positions:
                merged_jobs[positions[identity]] = job
            else:
                if identity is not None:
                    positions[identity] = len(merged_jobs)
                merged_jobs.append(job)

    active_jobs = []
    inactive_jobs = []
    for job in merged_jobs:
        try:
            status = int(job.get("status"))
        except (TypeError, ValueError):
            continue
        if status in {0, 3}:
            active_jobs.append(job)
        elif status in {1, 2}:
            inactive_jobs.append(job)
    return active_jobs, inactive_jobs


class JobRow(QFrame):
    clicked = pyqtSignal(dict)

    def __init__(self, job, job_type, parent=None):
        super().__init__(parent)
        self.job = job
        self.setObjectName("jobRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(74)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 12, 10)
        layout.setSpacing(11)

        icon = QLabel()
        icon.setObjectName("jobIcon")
        icon.setFixedSize(38, 38)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(qta.icon("fa5s.briefcase", color=BLUE).pixmap(QSize(17, 17)))
        layout.addWidget(icon)

        copy = QVBoxLayout()
        copy.setSpacing(3)
        title = QLabel(job.get("title") or "Untitled job")
        title.setObjectName("jobTitle")
        title.setWordWrap(True)
        subtitle = QLabel(format_chat_time(job.get("created_time")) or "No creation time")
        subtitle.setObjectName("jobMeta")
        copy.addWidget(title)
        copy.addWidget(subtitle)
        layout.addLayout(copy, 1)

        status = QLabel(job_status_text(job, job_type))
        status.setObjectName(f"jobStatus{job_status_text(job, job_type)}")
        layout.addWidget(status)
        arrow = QLabel()
        arrow.setPixmap(qta.icon("fa5s.chevron-right", color="#91A2B8").pixmap(QSize(10, 14)))
        layout.addWidget(arrow)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit(self.job)
        super().mouseReleaseEvent(event)


class JobDetail(QFrame):
    back_requested = pyqtSignal()

    def __init__(self, job, job_type, current_progress=None, parent=None):
        super().__init__(parent)
        self.job_id = job.get("id")
        self.setObjectName("jobsPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        back = QPushButton("Back to jobs")
        back.setObjectName("backButton")
        back.setIcon(qta.icon("fa5s.arrow-left", color=BLUE))
        back.clicked.connect(self.back_requested)
        header.addWidget(back)
        header.addStretch()
        layout.addLayout(header)

        title = QLabel(job.get("title") or "Untitled job")
        title.setObjectName("jobDetailTitle")
        title.setWordWrap(True)
        layout.addWidget(title)
        layout.addSpacing(5)

        layout.addWidget(
            self._detail_field(
                "1. Task objectives", format_task_objectives(job.get("task"))
            )
        )
        progress_field, self.progress_value = self._detail_field(
            "2. Current progress",
            display_job_value(current_progress) if current_progress is not None else "Waiting for progress…",
            return_value=True,
        )
        layout.addWidget(progress_field)
        layout.addStretch()

    @staticmethod
    def _detail_field(label_text, value_text, return_value=False):
        field = QFrame()
        field.setObjectName("jobField")
        field_layout = QVBoxLayout(field)
        field_layout.setContentsMargins(14, 12, 14, 13)
        field_layout.setSpacing(7)
        label = QLabel(label_text)
        label.setObjectName("jobFieldName")
        value = QLabel(value_text or "—")
        value.setObjectName("jobFieldValue")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        field_layout.addWidget(label)
        field_layout.addWidget(value)
        return (field, value) if return_value else field

    def update_progress(self, current_progress):
        self.progress_value.setText(display_job_value(current_progress))


class JobsPanel(QFrame):
    def __init__(self, active_jobs=None, inactive_jobs=None, job_progress=None, parent=None):
        super().__init__(parent)
        self.setObjectName("jobsPanel")
        self.active_jobs = active_jobs or []
        self.inactive_jobs = inactive_jobs or []
        self.job_progress = job_progress if job_progress is not None else {}
        self.current_detail = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.pages = QStackedWidget()
        self.pages.setObjectName("jobsPages")
        self.list_page = self.build_list_page()
        self.pages.addWidget(self.list_page)
        root.addWidget(self.pages)

    def build_list_page(self):
        page = QFrame()
        page.setObjectName("jobsPanel")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title = QLabel("Jobs")
        title.setObjectName("sectionTitle")
        subtitle = QLabel("Track active work and review completed jobs")
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        tabs = QHBoxLayout()
        tabs.setSpacing(8)
        self.active_tab = QPushButton(f"Active  {len(self.active_jobs)}")
        self.inactive_tab = QPushButton(f"Inactive  {len(self.inactive_jobs)}")
        self.active_tab.setObjectName("jobTabActive")
        self.inactive_tab.setObjectName("jobTab")
        set_selected_elevation(self.active_tab, True)
        self.active_tab.clicked.connect(lambda: self.switch_tab(0))
        self.inactive_tab.clicked.connect(lambda: self.switch_tab(1))
        tabs.addWidget(self.active_tab)
        tabs.addWidget(self.inactive_tab)
        tabs.addStretch()
        layout.addLayout(tabs)

        self.job_lists = QStackedWidget()
        self.job_lists.setObjectName("jobLists")
        self.job_lists.addWidget(self.build_job_list(self.active_jobs, "active"))
        self.job_lists.addWidget(self.build_job_list(self.inactive_jobs, "inactive"))
        layout.addWidget(self.job_lists, 1)
        return page

    def build_job_list(self, jobs, job_type):
        scroll = QScrollArea()
        scroll.setObjectName("jobListScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("jobListContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 2, 4, 4)
        layout.setSpacing(9)
        if jobs:
            for job in jobs:
                if not isinstance(job, dict):
                    continue
                row = JobRow(job, job_type)
                row.clicked.connect(lambda selected, kind=job_type: self.show_detail(selected, kind))
                layout.addWidget(row)
            layout.addStretch()
        else:
            layout.addStretch()
            empty = QLabel(f"No {job_type} jobs")
            empty.setObjectName("emptyTitle")
            empty.setAlignment(Qt.AlignCenter)
            layout.addWidget(empty)
            layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def switch_tab(self, index):
        self.job_lists.setCurrentIndex(index)
        self.active_tab.setObjectName("jobTabActive" if index == 0 else "jobTab")
        self.inactive_tab.setObjectName("jobTabActive" if index == 1 else "jobTab")
        set_selected_elevation(self.active_tab, index == 0)
        set_selected_elevation(self.inactive_tab, index == 1)
        for button in (self.active_tab, self.inactive_tab):
            button.style().unpolish(button)
            button.style().polish(button)

    def show_detail(self, job, job_type):
        detail = JobDetail(
            job,
            job_type,
            current_progress=self.job_progress.get(job.get("id")),
        )
        detail.back_requested.connect(self.show_list)
        self.current_detail = detail
        self.pages.addWidget(detail)
        self.pages.setCurrentWidget(detail)

    def show_list(self):
        current = self.pages.currentWidget()
        self.pages.setCurrentWidget(self.list_page)
        if current is not self.list_page:
            self.pages.removeWidget(current)
            current.deleteLater()
        self.current_detail = None

    def update_job_progress(self, job_id, current_progress):
        self.job_progress[job_id] = current_progress
        if self.current_detail is not None and self.current_detail.job_id == job_id:
            self.current_detail.update_progress(current_progress)

    def update_active_jobs(self, active_jobs):
        self.active_jobs = active_jobs or []
        self.active_tab.setText(f"Active  {len(self.active_jobs)}")
        current_index = self.job_lists.currentIndex()
        old_list = self.job_lists.widget(0)
        replacement = self.build_job_list(self.active_jobs, "active")
        self.job_lists.insertWidget(0, replacement)
        self.job_lists.removeWidget(old_list)
        old_list.deleteLater()
        self.job_lists.setCurrentIndex(current_index)

    def update_inactive_jobs(self, inactive_jobs):
        self.inactive_jobs = inactive_jobs or []
        self.inactive_tab.setText(f"Inactive  {len(self.inactive_jobs)}")
        current_index = self.job_lists.currentIndex()
        old_list = self.job_lists.widget(1)
        replacement = self.build_job_list(self.inactive_jobs, "inactive")
        self.job_lists.insertWidget(1, replacement)
        self.job_lists.removeWidget(old_list)
        old_list.deleteLater()
        self.job_lists.setCurrentIndex(current_index)


class JobProgressBubble(QFrame):
    def __init__(self, job, current_progress=None, started_at=None, parent=None):
        super().__init__(parent)
        self.setObjectName("jobProgressCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 12)
        layout.setSpacing(6)
        title = QLabel(f'Job title: "{job.get("title") or "Untitled job"}" is processing')
        title.setObjectName("jobProgressTitle")
        title.setWordWrap(True)
        self.started_at = started_at or datetime.now()
        self.worked_for = QLabel()
        self.worked_for.setObjectName("jobWorkedFor")
        self.worked_for.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        title_row = QHBoxLayout()
        title_row.setSpacing(10)
        title_row.addWidget(title, 1)
        title_row.addWidget(self.worked_for, 0, Qt.AlignTop)
        label = QLabel("Current Progress:")
        label.setObjectName("jobProgressLabel")
        label_row = QHBoxLayout()
        label_row.setSpacing(4)
        label_row.addWidget(label)
        label_row.addWidget(AnimatedDots())
        label_row.addStretch()
        progress = QLabel(
            display_job_value(current_progress)
            if current_progress is not None
            else "Waiting for progress…"
        )
        progress.setObjectName("jobProgressText")
        progress.setWordWrap(True)
        progress.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addLayout(title_row)
        layout.addLayout(label_row)
        layout.addWidget(progress)
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(1000)
        self.elapsed_timer.timeout.connect(self.update_elapsed_time)
        self.elapsed_timer.start()
        self.update_elapsed_time()

    def update_elapsed_time(self):
        elapsed = max(0, int((datetime.now() - self.started_at).total_seconds())) + 1
        minutes, seconds = divmod(elapsed, 60)
        duration = f"{minutes}m {seconds}s" if minutes else f"{seconds}s"
        self.worked_for.setText(f"Worked for {duration}")


class ChatPanel(QFrame):
    message_submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()
    load_more_requested = pyqtSignal()

    def __init__(
        self,
        chat_messages=None,
        newest_first=False,
        processing_jobs=None,
        job_progress=None,
        job_started_at=None,
        agent_typing=False,
        agent_typing_summary="Thinking",
        learning_mode=False,
        scroll_to_bottom_on_show=False,
        initial_scroll_value=None,
        preserve_bottom_on_refresh=False,
        has_more_messages=False,
        history_scroll_anchor=None,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("chatPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.learning_banner = QLabel(
            'LEARNING MODE — Send "start" to start teaching the AI. '
            "Confirmed flows are saved as learning jobs, not operational tasks."
        )
        self.learning_banner.setObjectName("learningBanner")
        self.learning_banner.setAlignment(Qt.AlignCenter)
        self.learning_banner.setWordWrap(True)
        self.learning_banner.setVisible(bool(learning_mode))
        layout.addWidget(self.learning_banner)

        scroll = QScrollArea()
        scroll.setObjectName("chatScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        conversation = QWidget()
        conversation.setObjectName("conversation")
        messages = QVBoxLayout(conversation)
        messages.setContentsMargins(18, 20, 18, 96)
        messages.setSpacing(2)
        self.messages_layout = messages
        valid_messages = [item for item in (chat_messages or []) if isinstance(item, dict)]
        self.load_more_button = QPushButton("Load more")
        self.load_more_button.setObjectName("loadMoreButton")
        self.load_more_button.setCursor(Qt.PointingHandCursor)
        self.load_more_button.setVisible(False)
        self.load_more_button.clicked.connect(self.request_more_messages)
        messages.addWidget(self.load_more_button, 0, Qt.AlignHCenter)
        if valid_messages:
            ordered_messages = reversed(valid_messages) if newest_first else valid_messages
            for item in ordered_messages:
                text = item.get("message")
                if not isinstance(text, str) or not text.strip():
                    continue
                messages.addWidget(
                    MessageBubble(
                        text.strip(),
                        format_chat_time(item.get("created_time")),
                        outgoing=item.get("type") == "in",
                    )
                )
        else:
            self.empty_state = QWidget()
            empty_layout = QVBoxLayout(self.empty_state)
            empty_layout.addStretch()
            empty_icon = QLabel()
            empty_icon.setObjectName("emptyIcon")
            empty_icon.setPixmap(
                qta.icon("fa5s.comments", color="#A9BCD3").pixmap(QSize(38, 38))
            )
            empty_icon.setAlignment(Qt.AlignCenter)
            empty_title = QLabel("No messages yet")
            empty_title.setObjectName("emptyTitle")
            empty_title.setAlignment(Qt.AlignCenter)
            empty_text = QLabel("Start a conversation with Swarif")
            empty_text.setObjectName("emptyText")
            empty_text.setAlignment(Qt.AlignCenter)
            empty_layout.addWidget(empty_icon)
            empty_layout.addWidget(empty_title)
            empty_layout.addWidget(empty_text)
            empty_layout.addStretch()
            messages.addWidget(self.empty_state, 1)
        if valid_messages:
            self.empty_state = None

        self.typing_indicator = TypingIndicator(agent_typing_summary)
        self.typing_indicator.setVisible(bool(agent_typing))
        messages.addWidget(self.typing_indicator)

        self.processing_container = QWidget()
        self.processing_layout = QVBoxLayout(self.processing_container)
        self.processing_layout.setContentsMargins(0, 8, 0, 8)
        self.processing_layout.setSpacing(9)
        messages.addWidget(self.processing_container)
        messages.addItem(QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.update_processing_jobs(
            processing_jobs or [], job_progress or {}, job_started_at or {}
        )
        scroll.setWidget(conversation)
        layout.addWidget(scroll, 1)
        self.chat_scroll = scroll
        self.has_more_messages = bool(has_more_messages)
        scroll.verticalScrollBar().valueChanged.connect(self.update_scroll_controls)
        scroll.verticalScrollBar().rangeChanged.connect(self.update_scroll_controls)
        self.initial_scroll_active = bool(scroll_to_bottom_on_show)
        if scroll_to_bottom_on_show:
            scroll.verticalScrollBar().rangeChanged.connect(
                self.keep_opening_at_bottom
            )
            QTimer.singleShot(0, self.scroll_to_bottom)
            QTimer.singleShot(1000, self.finish_initial_scroll)
        elif preserve_bottom_on_refresh:
            scroll.verticalScrollBar().rangeChanged.connect(
                self.keep_refresh_at_bottom
            )
            QTimer.singleShot(0, self.scroll_to_bottom)
            QTimer.singleShot(500, self.finish_refresh_at_bottom)
        elif initial_scroll_value is not None:
            self._scroll_restore_value = int(initial_scroll_value)
            scroll.verticalScrollBar().rangeChanged.connect(
                self.keep_refresh_position
            )
            QTimer.singleShot(0, self.keep_refresh_position)
            QTimer.singleShot(500, self.finish_refresh_position)
        elif history_scroll_anchor is not None:
            old_value, old_maximum = history_scroll_anchor
            self._history_anchor_value = int(old_value)
            self._history_anchor_maximum = int(old_maximum)
            scroll.verticalScrollBar().rangeChanged.connect(self.keep_history_position)
            QTimer.singleShot(0, self.keep_history_position)
            QTimer.singleShot(500, self.finish_history_position)

        self.jump_to_bottom_button = QPushButton(self.chat_scroll.viewport())
        self.jump_to_bottom_button.setObjectName("jumpToBottomButton")
        self.jump_to_bottom_button.setIcon(
            qta.icon("fa5s.chevron-down", color="#40516A")
        )
        self.jump_to_bottom_button.setIconSize(QSize(13, 13))
        self.jump_to_bottom_button.setFixedSize(34, 34)
        self.jump_to_bottom_button.setCursor(Qt.PointingHandCursor)
        self.jump_to_bottom_button.setToolTip("Jump to latest message")
        self.jump_to_bottom_button.setAccessibleName("Jump to latest message")
        jump_shadow = QGraphicsDropShadowEffect(self.jump_to_bottom_button)
        jump_shadow.setBlurRadius(14)
        jump_shadow.setOffset(0, 3)
        jump_shadow.setColor(QColor(38, 67, 103, 70))
        self.jump_to_bottom_button.setGraphicsEffect(jump_shadow)
        self.jump_to_bottom_button.clicked.connect(self.scroll_to_bottom)
        self.jump_to_bottom_button.hide()
        self.chat_scroll.viewport().installEventFilter(self)
        self.position_jump_to_bottom_button()

        composer_wrap = QWidget(self)
        composer_wrap.setObjectName("composerWrap")
        composer_wrap.setAttribute(Qt.WA_TranslucentBackground)
        composer_wrap.setAutoFillBackground(False)
        composer_layout = QVBoxLayout(composer_wrap)
        composer_layout.setContentsMargins(15, 10, 15, 16)
        composer = Composer()
        self.composer = composer
        composer.message_submitted.connect(self.message_submitted)
        composer.stop_requested.connect(self.stop_requested)
        composer.message.height_changed.connect(self.update_composer_height)
        composer_layout.addWidget(composer)
        self.composer_wrap = composer_wrap
        self.update_composer_height(composer.message.height())
        composer_wrap.show()
        composer_wrap.raise_()
        QTimer.singleShot(0, self.position_composer)
        QTimer.singleShot(0, self.update_scroll_controls)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "composer_wrap"):
            self.position_composer()

    def position_composer(self):
        margin = 0
        height = self.composer_wrap.height()
        self.composer_wrap.setGeometry(
            margin,
            max(0, self.height() - height - margin),
            max(0, self.width() - margin * 2),
            height,
        )
        self.composer_wrap.raise_()
        if hasattr(self, "jump_to_bottom_button"):
            self.position_jump_to_bottom_button()

    def update_composer_height(self, editor_height):
        if not hasattr(self, "composer_wrap"):
            return
        wrapper_height = max(84, int(editor_height) + 40)
        self.composer_wrap.setFixedHeight(wrapper_height)
        margins = self.messages_layout.contentsMargins()
        self.messages_layout.setContentsMargins(
            margins.left(),
            margins.top(),
            margins.right(),
            wrapper_height + 12,
        )
        self.position_composer()

    def scroll_to_bottom(self):
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_scroll_controls(self, *_args):
        scrollbar = self.chat_scroll.verticalScrollBar()
        value = scrollbar.value()
        self.load_more_button.setVisible(
            self.has_more_messages and int(value) <= 2
        )
        away_from_bottom = value < max(0, scrollbar.maximum() - 2)
        self.jump_to_bottom_button.setVisible(away_from_bottom)
        if away_from_bottom:
            self.position_jump_to_bottom_button()
            self.jump_to_bottom_button.raise_()

    def position_jump_to_bottom_button(self):
        viewport = self.chat_scroll.viewport()
        right_margin = 22
        bottom_margin = 14
        target_y = viewport.height() - self.jump_to_bottom_button.height() - bottom_margin
        if hasattr(self, "composer_wrap"):
            viewport_top = viewport.mapTo(self, QPoint(0, 0)).y()
            capsule_top = self.composer_wrap.y() + 10
            target_y = capsule_top - viewport_top - self.jump_to_bottom_button.height() - 8
        self.jump_to_bottom_button.move(
            max(right_margin, viewport.width() - self.jump_to_bottom_button.width() - right_margin),
            max(14, target_y),
        )

    def eventFilter(self, watched, event):
        if watched is self.chat_scroll.viewport() and event.type() == QEvent.Resize:
            self.position_jump_to_bottom_button()
        return super().eventFilter(watched, event)

    def request_more_messages(self):
        self.load_more_button.setEnabled(False)
        self.load_more_button.setText("Loading…")
        self.load_more_requested.emit()

    def show_load_more_error(self):
        self.load_more_button.setEnabled(True)
        self.load_more_button.setText("Couldn't load — try again")

    def keep_history_position(self, *_args):
        scrollbar = self.chat_scroll.verticalScrollBar()
        added_height = max(0, scrollbar.maximum() - self._history_anchor_maximum)
        scrollbar.setValue(self._history_anchor_value + added_height)

    def finish_history_position(self):
        self.keep_history_position()
        try:
            self.chat_scroll.verticalScrollBar().rangeChanged.disconnect(
                self.keep_history_position
            )
        except (TypeError, RuntimeError):
            pass

    def keep_opening_at_bottom(self, _minimum, maximum):
        self.chat_scroll.verticalScrollBar().setValue(maximum)

    def finish_initial_scroll(self):
        self.scroll_to_bottom()
        self.initial_scroll_active = False
        try:
            self.chat_scroll.verticalScrollBar().rangeChanged.disconnect(
                self.keep_opening_at_bottom
            )
        except (TypeError, RuntimeError):
            pass

    def restore_scroll(self, value):
        self.chat_scroll.verticalScrollBar().setValue(int(value))

    def keep_refresh_at_bottom(self, *_args):
        self.scroll_to_bottom()

    def finish_refresh_at_bottom(self):
        self.scroll_to_bottom()
        try:
            self.chat_scroll.verticalScrollBar().rangeChanged.disconnect(
                self.keep_refresh_at_bottom
            )
        except (TypeError, RuntimeError):
            pass

    def keep_refresh_position(self, *_args):
        self.restore_scroll(self._scroll_restore_value)

    def finish_refresh_position(self):
        self.keep_refresh_position()
        try:
            self.chat_scroll.verticalScrollBar().rangeChanged.disconnect(
                self.keep_refresh_position
            )
        except (TypeError, RuntimeError):
            pass

    def set_agent_typing(self, typing, summary="Thinking"):
        self.typing_indicator.set_summary(summary)
        self.typing_indicator.setVisible(bool(typing))

    def set_task_state(self, state):
        self.composer.set_task_state(state)

    def update_processing_jobs(self, processing_jobs, job_progress, job_started_at=None):
        while self.processing_layout.count():
            item = self.processing_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for job in processing_jobs:
            job_id = job.get("id")
            self.processing_layout.addWidget(
                JobProgressBubble(
                    job,
                    job_progress.get(job_id),
                    (job_started_at or {}).get(str(job_id)),
                )
            )
        self.processing_container.setVisible(bool(processing_jobs))
        if self.empty_state is not None:
            self.empty_state.setVisible(not processing_jobs)


class ConnectionLogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("TCP connection log")
        self.resize(720, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        title = QLabel("TCP connection log")
        title.setObjectName("sectionTitle")
        description = QLabel("Live packets and connection events sent between Swarif and the agent server.")
        description.setObjectName("settingsHelp")
        self.output = QPlainTextEdit()
        self.output.setObjectName("connectionLogOutput")
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("No connection activity yet.")

        actions = QHBoxLayout()
        actions.addStretch()
        close = QPushButton("Close")
        close.setObjectName("settingsSecondary")
        close.clicked.connect(self.close)
        actions.addWidget(close)

        layout.addWidget(title)
        layout.addWidget(description)
        layout.addWidget(self.output, 1)
        layout.addLayout(actions)

    def set_entries(self, entries):
        self.output.setPlainText("\n".join(entries))
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())

    def append_entry(self, entry):
        self.output.appendPlainText(entry)
        self.output.verticalScrollBar().setValue(self.output.verticalScrollBar().maximum())


class SettingsPanel(QFrame):
    saved = pyqtSignal(str, int, bool)
    connection_log_requested = pyqtSignal()
    learning_mode_changed = pyqtSignal(bool)

    def __init__(
        self,
        agent_ip="",
        server_port=8767,
        agent_is_local=False,
        learning_mode=False,
        parent=None,
    ):
        super().__init__(parent)
        self.setObjectName("settingsPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)

        title = QLabel("Settings")
        title.setObjectName("sectionTitle")
        subtitle = QLabel("Connect Files to your remote Swarif agent")
        subtitle.setObjectName("sectionSubtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)
        layout.addSpacing(18)

        learning_title = QLabel("Learning mode")
        learning_title.setObjectName("settingsSectionTitle")
        learning_description = QLabel(
            "Teach Swarif your company's preferences, procedures, and workflows."
        )
        learning_description.setObjectName("settingsHelp")
        learning_description.setWordWrap(True)
        self.learning_toggle = ToggleSwitch("Learning mode")
        self.learning_toggle.setObjectName("learningToggle")
        self.learning_toggle.setChecked(bool(learning_mode))
        self.learning_toggle.toggled.connect(self.learning_mode_changed)
        layout.addWidget(learning_title)
        layout.addWidget(learning_description)
        layout.addWidget(self.learning_toggle)
        layout.addSpacing(18)

        label = QLabel("Agent IP address")
        label.setObjectName("fieldLabel")
        self.agent_ip = QLineEdit(agent_ip)
        self.agent_ip.setObjectName("settingsInput")
        self.agent_ip.setFixedHeight(43)
        self.agent_ip.setPlaceholderText("e.g. 192.168.1.50")
        self.agent_ip.returnPressed.connect(self.save)
        self._remote_agent_ip = "" if agent_is_local else agent_ip
        self.local_agent = ToggleSwitch("Use localhost")
        self.local_agent.setObjectName("settingsCheckbox")
        self.local_agent.setChecked(bool(agent_is_local))
        self.local_agent.toggled.connect(self.set_local_agent)
        port_label = QLabel("Agent port")
        port_label.setObjectName("fieldLabel")
        self.server_port = QLineEdit(str(server_port or 8767))
        self.server_port.setObjectName("settingsInput")
        self.server_port.setFixedHeight(43)
        self.server_port.setPlaceholderText("8767")
        self.server_port.returnPressed.connect(self.save)
        help_text = QLabel("The local agent connection defaults to TCP port 8767")
        help_text.setObjectName("settingsHelp")
        self.feedback = QLabel("")
        self.feedback.setObjectName("settingsFeedback")
        self.feedback.hide()
        save = QPushButton("Save settings")
        save.setObjectName("settingsSave")
        save.setCursor(Qt.PointingHandCursor)
        save.clicked.connect(self.save)

        layout.addWidget(label)
        layout.addWidget(self.agent_ip)
        layout.addWidget(self.local_agent)
        layout.addSpacing(6)
        layout.addWidget(port_label)
        layout.addWidget(self.server_port)
        layout.addWidget(help_text)
        layout.addSpacing(8)
        layout.addWidget(save)
        layout.addWidget(self.feedback)
        connection_log = QPushButton("View connection log")
        connection_log.setObjectName("settingsSecondary")
        connection_log.setCursor(Qt.PointingHandCursor)
        connection_log.clicked.connect(self.connection_log_requested)
        layout.addWidget(connection_log)
        layout.addSpacing(22)

        local_llm_title = QLabel("Local LLM")
        local_llm_title.setObjectName("settingsSectionTitle")
        local_llm_description = QLabel(
            "Optionally run AI models on this computer. A local LLM is not required to use Swarif."
        )
        local_llm_description.setObjectName("settingsHelp")
        local_llm_description.setWordWrap(True)
        setup_local_llm = QPushButton("Set up local LLM")
        setup_local_llm.setObjectName("settingsSecondary")
        setup_local_llm.setCursor(Qt.PointingHandCursor)
        setup_local_llm.clicked.connect(self.show_local_llm_placeholder)
        self.local_llm_feedback = QLabel("")
        self.local_llm_feedback.setObjectName("settingsHelp")
        self.local_llm_feedback.hide()

        layout.addWidget(local_llm_title)
        layout.addWidget(local_llm_description)
        layout.addSpacing(4)
        layout.addWidget(setup_local_llm)
        layout.addWidget(self.local_llm_feedback)
        layout.addStretch()
        self.set_local_agent(self.local_agent.isChecked())

    def save(self):
        is_local = self.local_agent.isChecked()
        value = "127.0.0.1" if is_local else self.agent_ip.text().strip()
        if not is_local:
            try:
                ipaddress.IPv4Address(value)
            except ValueError:
                self.feedback.setText("Enter a valid IPv4 address.")
                self.feedback.setProperty("error", True)
                self.feedback.style().unpolish(self.feedback)
                self.feedback.style().polish(self.feedback)
                self.feedback.show()
                return
        try:
            port = int(self.server_port.text().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.feedback.setText("Enter a valid port between 1 and 65535.")
            self.feedback.setProperty("error", True)
            self.feedback.style().unpolish(self.feedback)
            self.feedback.style().polish(self.feedback)
            self.feedback.show()
            return
        self.saved.emit(value, port, is_local)

    def set_local_agent(self, is_local):
        if is_local:
            current_value = self.agent_ip.text().strip()
            if current_value and current_value != "127.0.0.1":
                self._remote_agent_ip = current_value
            self.agent_ip.setText("127.0.0.1")
            self.agent_ip.setDisabled(True)
            self.agent_ip.setPlaceholderText("127.0.0.1")
        else:
            self.agent_ip.setDisabled(False)
            if self.agent_ip.text().strip() == "127.0.0.1":
                self.agent_ip.setText(self._remote_agent_ip)
            self.agent_ip.setPlaceholderText("e.g. 192.168.1.50")

    def show_saved(self):
        self.feedback.setText("Agent connection settings saved.")
        self.feedback.setProperty("error", False)
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        self.feedback.show()

    def show_local_llm_placeholder(self):
        self.local_llm_feedback.setText("Local LLM setup will be available in a future update.")
        self.local_llm_feedback.show()


class LoadingOverlay(QFrame):
    """Animated, input-blocking overlay shown while a section is loading."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loadingOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        card = QFrame()
        card.setObjectName("loadingCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(24, 18, 24, 18)
        card_layout.setSpacing(10)
        self.spinner = QLabel()
        self.spinner.setAlignment(Qt.AlignCenter)
        self.spinner.setFixedSize(30, 30)
        self.label = QLabel("Loading…")
        self.label.setObjectName("loadingLabel")
        self.label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.spinner, 0, Qt.AlignCenter)
        card_layout.addWidget(self.label)
        layout.addWidget(card, 0, Qt.AlignCenter)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(70)
        self._timer.timeout.connect(self.advance)

    def start(self, message):
        self.label.setText(message)
        self._angle = 0
        self.advance()
        self.show()
        self.raise_()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def advance(self):
        self._angle = (self._angle + 30) % 360
        pixmap = qta.icon("fa5s.spinner", color=BLUE).pixmap(QSize(24, 24))
        self.spinner.setPixmap(
            pixmap.transformed(QTransform().rotate(self._angle), Qt.SmoothTransformation)
        )


class SwarifWindow(QWidget):
    message_submitted = pyqtSignal(str)
    stop_requested = pyqtSignal()
    server_connection_changed = pyqtSignal()
    learning_mode_changed = pyqtSignal(bool)
    section_data_loaded = pyqtSignal(int, str, object)

    def __init__(self):
        super().__init__()
        self._server_connected = False
        self.sidebar = None
        self._job_progress = {}
        self._job_processing_started = {}
        self._active_jobs = []
        self._connection_log = []
        self._connection_log_size = 0
        self._connection_log_dialog = None
        self._agent_typing = False
        self._agent_typing_summary = "Thinking"
        self._task_state = "idle"
        self._learning_mode = False
        self._chat_history_limit = 20
        self._chat_has_more = False
        self._tcp_processing_job_ids = set()
        self._section_request_id = 0
        self._initial_position_pending = True
        self._inactive_jobs = []
        self.jobs_panel = None
        self.files_panel = None
        self._compact = False
        self._expanded_size = QSize(760, 700)
        self._expanded_position = None
        self.setWindowTitle("Swarif")
        self.setWindowIcon(QIcon(str(static_asset("Swarif_Logo_BG.png"))))
        self.setObjectName("swarifWindow")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(760, 700)
        self.setMinimumSize(500, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(0)

        card = QFrame()
        card.setObjectName("windowCard")
        self.window_card = card
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(34)
        shadow.setOffset(0, 9)
        shadow.setColor(QColor(34, 83, 142, 68))
        card.setGraphicsEffect(shadow)

        self.card_layout = QVBoxLayout(card)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(0)

        self.title_bar = TitleBar()
        self.title_bar.minimize_clicked.connect(self.toggle_compact)
        self.title_bar.close_clicked.connect(self.close)
        self.card_layout.addWidget(self.title_bar)
        self.current_page = None
        root.addWidget(card)

        self.setStyleSheet(STYLESHEET)
        QTimer.singleShot(0, self.update_window_card_mask)
        self.loading_overlay = LoadingOverlay(self)
        self.loading_overlay.hide()
        self.section_data_loaded.connect(self.finish_section_loading)
        startup_session = Agent.read_session()
        if startup_session and startup_session.get("remember_me") is True:
            self.show_home()
        else:
            if startup_session:
                Agent.clear_session()
            self.show_login()

        primary_screen = QApplication.primaryScreen()
        if primary_screen is not None:
            primary_screen.availableGeometryChanged.connect(self.fit_to_available_screen)
            QTimer.singleShot(0, self.fit_to_available_screen)

    def set_page(self, page):
        if self.current_page is not None:
            self.card_layout.removeWidget(self.current_page)
            self.current_page.setParent(None)
            self.current_page.deleteLater()
        self.current_page = page
        self.card_layout.addWidget(page, 1)
        page.setVisible(not self._compact)

    def toggle_compact(self):
        """Collapse to a floating control or restore the window in place."""
        self._compact = not self._compact
        self.setProperty("compact", self._compact)
        if self._compact:
            self._expanded_size = self.size()
            self._expanded_position = self.pos()
            if self.current_page is not None:
                self.current_page.hide()
            self.title_bar.set_compact(True)
            self.setMinimumSize(170, 96)
            self.setMaximumSize(170, 96)
            self.resize(170, 96)
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        else:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
            self.setMaximumSize(16777215, 16777215)
            self.setMinimumSize(500, 520)
            self.resize(self._expanded_size)
            self.title_bar.set_compact(False)
            if self.current_page is not None:
                self.current_page.show()
            self.fit_to_available_screen()
        self.style().unpolish(self)
        self.style().polish(self)
        self.title_bar.style().unpolish(self.title_bar)
        self.title_bar.style().polish(self.title_bar)
        self.show()
        if self._compact:
            QTimer.singleShot(0, self.position_bottom_right)
        elif self._expanded_position is not None:
            restored_position = QPoint(self._expanded_position)
            QTimer.singleShot(0, lambda: self.move(restored_position))

    def show_login(self):
        self.cancel_section_loading()
        login = LoginPage()
        login.login_requested.connect(self.attempt_login)
        self.set_page(login)

    def attempt_login(self):
        if not isinstance(self.current_page, LoginPage):
            return

        email = self.current_page.email.text()
        password = self.current_page.password.text()
        try:
            Agent.login(email, password, self.current_page.remember.isChecked())
        except (ValueError, RuntimeError, ConnectionError) as error:
            log(error)
            self.current_page.show_error(str(error))
            return

        self.server_connection_changed.emit()
        self.show_home()

    def begin_section_loading(self, message):
        self._section_request_id += 1
        self.position_loading_overlay()
        self.loading_overlay.start(message)
        return self._section_request_id

    def position_loading_overlay(self):
        card_rect = self.window_card.geometry()
        top = card_rect.top() + self.title_bar.height()
        left = card_rect.left()
        width = card_rect.width()
        if (
            self.current_page is not None
            and self.current_page.objectName() in {"homePage", "jobsPage", "settingsPage"}
        ):
            left += 190
            width -= 190
        self.loading_overlay.setGeometry(
            left,
            top,
            max(0, width),
            max(0, card_rect.bottom() - top + 1),
        )

    def cancel_section_loading(self):
        self._section_request_id += 1
        self.loading_overlay.stop()

    def finish_section_loading(self, request_id, section, payload):
        if request_id != self._section_request_id:
            return
        if section == "home":
            self.render_home(payload["session"], payload["messages"])
        elif section == "jobs":
            self.render_jobs(
                payload["session"],
                payload["active_jobs"],
                payload["inactive_jobs"],
            )
        self.loading_overlay.stop()

    def show_home(self):
        session = Agent.read_session()
        if not session:
            self.show_login()
            return

        request_id = self.begin_section_loading("Loading chat…")

        def worker():
            loaded_session = session
            if not isinstance(loaded_session.get("name"), str) or not loaded_session["name"].strip():
                try:
                    refreshed_session = Agent.refresh_session_user()
                    if refreshed_session:
                        loaded_session = refreshed_session
                except (RuntimeError, ConnectionError) as error:
                    log(error)
            try:
                chat_messages = Agent.sync_chat_from_database(
                    limit=self._chat_history_limit
                )
            except (ValueError, RuntimeError, ConnectionError) as error:
                log(error)
                try:
                    chat_messages = Agent.read_local_chat()
                except RuntimeError as local_error:
                    log(local_error)
                    chat_messages = []
            self.section_data_loaded.emit(
                request_id,
                "home",
                {"session": loaded_session, "messages": chat_messages or []},
            )

        threading.Thread(target=worker, name="swarif-load-chat", daemon=True).start()

    def render_home(self, session, chat_messages):
        self._chat_has_more = len(chat_messages) >= self._chat_history_limit

        body = QFrame()
        body.setObjectName("homePage")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        fallback_name = session.get("email", "Swarif").split("@", 1)[0].title()
        sidebar = Sidebar(user_name=session.get("name") or fallback_name, active_page="chat", connected=self._server_connected)
        self.sidebar = sidebar
        sidebar.logout_clicked.connect(self.logout)
        sidebar.chat_clicked.connect(self.show_home)
        sidebar.jobs_clicked.connect(self.show_jobs)
        sidebar.files_clicked.connect(self.open_files)
        sidebar.settings_clicked.connect(self.show_settings)
        body_layout.addWidget(sidebar)
        self.home_layout = body_layout
        self.chat_panel = ChatPanel(
            chat_messages=chat_messages or [],
            newest_first=False,
            processing_jobs=self.processing_jobs(),
            job_progress=self._job_progress,
            job_started_at=self._job_processing_started,
            agent_typing=self._agent_typing,
            agent_typing_summary=self._agent_typing_summary,
            learning_mode=self._learning_mode,
            scroll_to_bottom_on_show=True,
            has_more_messages=self._chat_has_more,
        )
        self.chat_panel.message_submitted.connect(self.message_submitted)
        self.chat_panel.stop_requested.connect(self.stop_requested)
        self.chat_panel.load_more_requested.connect(self.load_more_chat)
        self.chat_panel.set_task_state(self.effective_composer_state())
        body_layout.addWidget(self.chat_panel, 1)
        self.set_page(body)

    def show_jobs(self):
        session = Agent.read_session()
        if not session:
            self.show_login()
            return

        request_id = self.begin_section_loading("Loading jobs…")

        def worker():
            try:
                active_jobs = Agent.fetch_jobs("active")
            except (ValueError, RuntimeError, ConnectionError) as error:
                log(error)
                active_jobs = []
            try:
                inactive_jobs = Agent.fetch_jobs("inactive")
            except (ValueError, RuntimeError, ConnectionError) as error:
                log(error)
                inactive_jobs = []
            self.section_data_loaded.emit(
                request_id,
                "jobs",
                {
                    "session": session,
                    "active_jobs": active_jobs,
                    "inactive_jobs": inactive_jobs,
                },
            )

        threading.Thread(target=worker, name="swarif-load-jobs", daemon=True).start()

    def render_jobs(self, session, active_jobs, inactive_jobs):

        self._active_jobs, self._inactive_jobs = partition_jobs_by_status(
            active_jobs, inactive_jobs
        )

        body = QFrame()
        body.setObjectName("jobsPage")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        fallback_name = session.get("email", "Swarif").split("@", 1)[0].title()
        sidebar = Sidebar(user_name=session.get("name") or fallback_name, active_page="jobs", connected=self._server_connected)
        self.sidebar = sidebar
        sidebar.logout_clicked.connect(self.logout)
        sidebar.chat_clicked.connect(self.show_home)
        sidebar.jobs_clicked.connect(self.show_jobs)
        sidebar.files_clicked.connect(self.open_files)
        sidebar.settings_clicked.connect(self.show_settings)
        body_layout.addWidget(sidebar)
        self.jobs_panel = JobsPanel(
            self._active_jobs, self._inactive_jobs, self._job_progress
        )
        body_layout.addWidget(self.jobs_panel, 1)
        self.set_page(body)

    def show_settings(self):
        self.cancel_section_loading()
        session = Agent.read_session()
        if not session:
            self.show_login()
            return

        body = QFrame()
        body.setObjectName("settingsPage")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        fallback_name = session.get("email", "Swarif").split("@", 1)[0].title()
        sidebar = Sidebar(user_name=session.get("name") or fallback_name, active_page="settings", connected=self._server_connected)
        self.sidebar = sidebar
        sidebar.logout_clicked.connect(self.logout)
        sidebar.chat_clicked.connect(self.show_home)
        sidebar.jobs_clicked.connect(self.show_jobs)
        sidebar.files_clicked.connect(self.open_files)
        sidebar.settings_clicked.connect(self.show_settings)
        body_layout.addWidget(sidebar)
        connection = Agent.connection_settings()
        settings = SettingsPanel(
            connection["agent_ip"],
            connection["server_port"],
            connection["agent_is_local"],
            self._learning_mode,
        )
        self.settings_panel = settings
        settings.learning_mode_changed.connect(self.set_learning_mode)
        settings.saved.connect(
            lambda agent_ip, server_port, agent_is_local: self.save_agent_connection(
                agent_ip, server_port, agent_is_local, settings
            )
        )
        settings.connection_log_requested.connect(self.show_connection_log)
        body_layout.addWidget(settings, 1)
        self.set_page(body)

    def append_connection_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self._connection_log.append(entry)
        self._connection_log_size += len((entry + "\n").encode("utf-8"))
        trimmed = False
        while (
            self._connection_log_size > MAX_CONNECTION_LOG_BYTES
            and len(self._connection_log) > 1
        ):
            removed = self._connection_log.pop(0)
            self._connection_log_size -= len((removed + "\n").encode("utf-8"))
            trimmed = True
        if self._connection_log_size > MAX_CONNECTION_LOG_BYTES:
            retained = (self._connection_log[0] + "\n").encode("utf-8")[-MAX_CONNECTION_LOG_BYTES:]
            self._connection_log[0] = retained.decode("utf-8", errors="ignore").rstrip("\n")
            self._connection_log_size = len(
                (self._connection_log[0] + "\n").encode("utf-8")
            )
            trimmed = True
        if self._connection_log_dialog is not None:
            if trimmed:
                self._connection_log_dialog.set_entries(self._connection_log)
            else:
                self._connection_log_dialog.append_entry(entry)

    def show_connection_log(self):
        if self._connection_log_dialog is None:
            self._connection_log_dialog = ConnectionLogDialog(self)
            self._connection_log_dialog.finished.connect(
                lambda _result: setattr(self, "_connection_log_dialog", None)
            )
        self._connection_log_dialog.set_entries(self._connection_log)
        self._connection_log_dialog.show()
        self._connection_log_dialog.raise_()
        self._connection_log_dialog.activateWindow()

    def save_agent_connection(self, agent_ip, server_port, agent_is_local, panel):
        try:
            Agent.save_connection_settings(
                agent_ip,
                server_port,
                agent_is_local,
            )
        except (ValueError, RuntimeError, OSError) as error:
            log(error)
            QMessageBox.warning(self, "Unable to save settings", str(error))
            return
        panel.show_saved()
        self.server_connection_changed.emit()

    def set_server_connected(self, connected):
        """Update connection widgets on the Qt UI thread."""
        self._server_connected = bool(connected)
        if self.sidebar is not None:
            self.sidebar.set_connected(self._server_connected)

    def set_agent_typing(self, typing, summary="Thinking"):
        self._agent_typing = bool(typing)
        if isinstance(summary, str) and summary.strip():
            self._agent_typing_summary = summary.strip()
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.chat_panel.set_agent_typing(
                self._agent_typing,
                self._agent_typing_summary,
            )
            self.refresh_composer_state()

    def set_task_state(self, state):
        self._task_state = state if state in {
            "idle", "thinking", "executing", "stopping"
        } else "idle"
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.refresh_composer_state()

    def effective_composer_state(self):
        if self._task_state == "stopping":
            return "stopping"
        if self._agent_typing or self._task_state == "thinking":
            return "thinking"
        if self.processing_jobs() or self._tcp_processing_job_ids:
            return "executing"
        return "idle"

    def refresh_composer_state(self):
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.chat_panel.set_task_state(self.effective_composer_state())

    @property
    def learning_mode(self):
        return self._learning_mode

    def set_learning_mode(self, enabled):
        self._learning_mode = bool(enabled)
        self.setProperty("learningMode", self._learning_mode)
        self.style().unpolish(self)
        self.style().polish(self)
        for widget in self.findChildren(QWidget):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.chat_panel.learning_banner.setVisible(self._learning_mode)
        if self.current_page is not None and self.current_page.objectName() == "settingsPage":
            if self.settings_panel.learning_toggle.isChecked() != self._learning_mode:
                self.settings_panel.learning_toggle.setChecked(self._learning_mode)
        self.learning_mode_changed.emit(self._learning_mode)

    def update_job_progress(self, job_id, current_progress):
        """Keep and display the newest TCP progress value for a job."""
        self._job_progress[job_id] = current_progress
        if job_id is not None:
            self._tcp_processing_job_ids.add(str(job_id))
        if (
            self.current_page is not None
            and self.current_page.objectName() == "jobsPage"
            and self.jobs_panel is not None
        ):
            self.jobs_panel.update_job_progress(job_id, current_progress)
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.chat_panel.update_processing_jobs(
                self.processing_jobs(),
                self._job_progress,
                self._job_processing_started,
            )
            self.refresh_composer_state()

    def update_jobs(self, active_jobs, inactive_jobs):
        """Update both status-based job collections from the global poller."""
        self._inactive_jobs = inactive_jobs or []
        terminal_ids = {
            str(job.get("id"))
            for job in self._inactive_jobs
            if isinstance(job, dict) and job.get("id") is not None
        }
        self._tcp_processing_job_ids.difference_update(terminal_ids)
        for job_id in terminal_ids:
            self._job_processing_started.pop(job_id, None)
        self.update_active_jobs(active_jobs)
        if (
            self.current_page is not None
            and self.current_page.objectName() == "jobsPage"
            and self.jobs_panel is not None
        ):
            self.jobs_panel.update_inactive_jobs(self._inactive_jobs)

    def processing_jobs(self):
        jobs = [job for job in self._active_jobs if job_is_processing(job)]
        now = datetime.now()
        for job in jobs:
            job_id = job.get("id")
            if job_id is not None:
                self._job_processing_started.setdefault(str(job_id), now)
        return jobs

    def update_active_jobs(self, active_jobs):
        """Apply the latest five-second job snapshot on the UI thread."""
        self._active_jobs = active_jobs or []
        if (
            self.current_page is not None
            and self.current_page.objectName() == "jobsPage"
            and self.jobs_panel is not None
        ):
            self.jobs_panel.update_active_jobs(self._active_jobs)
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.chat_panel.update_processing_jobs(
                self.processing_jobs(),
                self._job_progress,
                self._job_processing_started,
            )
            self.refresh_composer_state()

    def open_files(self):
        session = Agent.read_session()
        if not session:
            self.show_login()
            return
        try:
            root_path = Agent.user_files_path()
        except (KeyError, RuntimeError) as error:
            log(error)
            QMessageBox.warning(self, "Unable to show Files", str(error))
            return

        body = QFrame()
        body.setObjectName("filesPage")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        fallback_name = session.get("email", "Swarif").split("@", 1)[0].title()
        sidebar = Sidebar(
            user_name=session.get("name") or fallback_name,
            active_page="files",
            connected=self._server_connected,
        )
        self.sidebar = sidebar
        sidebar.logout_clicked.connect(self.logout)
        sidebar.chat_clicked.connect(self.show_home)
        sidebar.jobs_clicked.connect(self.show_jobs)
        sidebar.files_clicked.connect(self.open_files)
        sidebar.settings_clicked.connect(self.show_settings)
        body_layout.addWidget(sidebar)
        self.files_panel = FilesPanel(root_path)
        body_layout.addWidget(self.files_panel, 1)
        self.set_page(body)

    @property
    def chat_history_limit(self):
        return self._chat_history_limit

    def load_more_chat(self):
        if self.current_page is None or self.current_page.objectName() != "homePage":
            return
        scrollbar = self.chat_panel.chat_scroll.verticalScrollBar()
        anchor = (scrollbar.value(), scrollbar.maximum())
        next_limit = self._chat_history_limit + 30
        try:
            messages = Agent.sync_chat_from_database(limit=next_limit)
            if messages is False:
                raise RuntimeError("No active chat session")
        except (ValueError, RuntimeError, ConnectionError) as error:
            log(error)
            self.chat_panel.show_load_more_error()
            return
        self._chat_history_limit = next_limit
        self._chat_has_more = len(messages) >= next_limit
        self.update_chat_messages(messages, history_scroll_anchor=anchor)

    def update_chat_messages(self, chat_messages, history_scroll_anchor=None):
        if self.current_page is None or self.current_page.objectName() != "homePage":
            return
        old_panel = self.chat_panel
        old_scrollbar = old_panel.chat_scroll.verticalScrollBar()
        scroll_value = old_scrollbar.value()
        was_at_bottom = scroll_value >= max(0, old_scrollbar.maximum() - 2)
        continue_initial_scroll = old_panel.initial_scroll_active
        self.home_layout.removeWidget(old_panel)
        old_panel.setParent(None)
        old_panel.deleteLater()
        self.chat_panel = ChatPanel(
            chat_messages=chat_messages or [],
            newest_first=False,
            processing_jobs=self.processing_jobs(),
            job_progress=self._job_progress,
            job_started_at=self._job_processing_started,
            agent_typing=self._agent_typing,
            agent_typing_summary=self._agent_typing_summary,
            learning_mode=self._learning_mode,
            scroll_to_bottom_on_show=continue_initial_scroll,
            initial_scroll_value=(
                None
                if continue_initial_scroll or history_scroll_anchor is not None
                else scroll_value
            ),
            preserve_bottom_on_refresh=(
                history_scroll_anchor is None
                and not continue_initial_scroll
                and was_at_bottom
            ),
            has_more_messages=self._chat_has_more,
            history_scroll_anchor=history_scroll_anchor,
        )
        self.chat_panel.message_submitted.connect(self.message_submitted)
        self.chat_panel.stop_requested.connect(self.stop_requested)
        self.chat_panel.load_more_requested.connect(self.load_more_chat)
        self.chat_panel.set_task_state(self.effective_composer_state())
        self.home_layout.addWidget(self.chat_panel, 1)

    def logout(self):
        Agent.clear_session()
        self.server_connection_changed.emit()
        self.show_login()

    def showEvent(self, event):
        super().showEvent(event)
        if self._initial_position_pending:
            self._initial_position_pending = False
            QTimer.singleShot(0, self.position_initially_bottom_right)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_window_card_mask()
        if hasattr(self, "loading_overlay"):
            self.position_loading_overlay()

    def update_window_card_mask(self):
        """Clip child panels so all four card corners remain rounded on Windows."""
        if not hasattr(self, "window_card") or self.window_card.width() < 1:
            return
        radius = 34 if self._compact else 24
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.window_card.rect()),
            radius,
            radius,
        )
        self.window_card.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def position_initially_bottom_right(self):
        """Place the first window show at bottom-right without anchoring it."""
        self.position_bottom_right()

    def position_bottom_right(self):
        """Move the current window size to the screen's bottom-right corner."""
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 18
        self.move(
            available.right() - self.width() - margin + 1,
            available.bottom() - self.height() - margin + 1,
        )

    def fit_to_available_screen(self):
        """Keep the complete window card within the usable desktop area."""
        if self._compact:
            return
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 18
        maximum_width = max(500, available.width() - margin * 2)
        maximum_height = max(560, available.height() - margin * 2)
        target_width = min(max(self.width(), 680), maximum_width)
        target_height = min(max(self.height(), 600), maximum_height)
        if self.size() != QSize(target_width, target_height):
            self.resize(target_width, target_height)


STYLESHEET = f"""
* {{
    font-family: "Helvetica Neue";
    color: {DARK};
}}
QMenu {{
    background: white;
    color: {DARK};
    border: 1px solid #D7E3F1;
    border-radius: 9px;
    padding: 6px;
}}
QMenu::item {{
    background: transparent;
    color: {DARK};
    border-radius: 6px;
    padding: 7px 28px 7px 10px;
}}
QMenu::item:selected {{
    background: #EAF3FF;
    color: {BLUE};
}}
QMenu::item:disabled {{ color: #A1ADBC; }}
QMenu::separator {{
    background: #E1E9F2;
    height: 1px;
    margin: 5px 7px;
}}
QDialog, QMessageBox, QInputDialog, QFileDialog {{
    background: white;
    color: {DARK};
}}
QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel {{
    background: transparent;
    color: {DARK};
}}
QDialog QLineEdit, QInputDialog QLineEdit, QFileDialog QLineEdit {{
    background: white;
    color: {DARK};
    border: 1px solid #C8D8EA;
    border-radius: 7px;
    padding: 6px 9px;
    selection-background-color: #DCEBFD;
    selection-color: {DARK};
}}
QDialog QPushButton, QMessageBox QPushButton, QInputDialog QPushButton, QFileDialog QPushButton {{
    background: white;
    color: {DARK};
    border: 1px solid #C8D8EA;
    border-radius: 8px;
    min-height: 30px;
    padding: 0 12px;
}}
QDialog QPushButton:hover, QMessageBox QPushButton:hover,
QInputDialog QPushButton:hover, QFileDialog QPushButton:hover {{
    background: #EAF3FF;
    color: {BLUE};
    border-color: #AFC9E8;
}}
QFileDialog QTreeView, QFileDialog QListView, QFileDialog QComboBox {{
    background: white;
    color: {DARK};
    border: 1px solid #D7E3F1;
    selection-background-color: #DCEBFD;
    selection-color: {DARK};
}}
QComboBox QAbstractItemView {{
    background: white;
    color: {DARK};
    border: 1px solid #D7E3F1;
    selection-background-color: #DCEBFD;
    selection-color: {DARK};
}}
QToolTip {{
    background: white;
    color: {DARK};
    border: 1px solid #C8D8EA;
    padding: 5px;
}}
#windowCard {{
    background: #F9FBFE;
    border: 1px solid #D7E3F1;
    border-radius: 24px;
}}
#titleBar {{
    background: #F7FAFE;
    border-top-left-radius: 24px;
    border-top-right-radius: 24px;
    border-bottom: 1px solid {BORDER};
}}
#brand {{ color: {BLUE}; font-size: 25px; font-weight: 700; }}
#windowButton {{
    background: transparent; border: none; color: #526176;
    font-size: 24px; width: 30px; height: 30px; border-radius: 15px;
}}
#windowButton:hover {{ background: #E9F1FB; color: #17243A; }}
#loadingOverlay {{
    background: rgba(21, 35, 58, 72); border: none;
    border-bottom-right-radius: 23px;
}}
#loadingCard {{
    background: white; border: 1px solid {BORDER}; border-radius: 16px;
}}
#loadingLabel {{ color: #40516A; font-size: 12px; font-weight: 650; }}
#sidebar {{
    background: #F7FAFE; border-right: 1px solid {BORDER};
    border-bottom-left-radius: 23px;
}}
#navButton, #navActive {{
    border: none; border-radius: 11px; padding: 0 13px;
    text-align: left; font-size: 14px; font-weight: 500;
}}
#navButton {{ background: transparent; color: #40516A; }}
#navButton:hover {{ background: #EDF4FD; color: {BLUE}; }}
#navActive {{ background: {BLUE}; color: white; font-weight: 600; }}
#logoutButton {{
    background: transparent; color: #526176; border: none; border-radius: 11px;
    padding: 0 13px; text-align: left; font-size: 14px; font-weight: 500;
}}
#logoutButton:hover {{ background: #FFF0F1; color: #D84B57; }}
#settingsIconButton, #settingsIconActive {{
    border: none; border-radius: 11px; padding: 0;
}}
#settingsIconButton {{ background: transparent; }}
#settingsIconButton:hover {{ background: #EDF4FD; }}
#settingsIconActive {{ background: {BLUE}; }}
#profile {{ background: #EEF5FE; border-radius: 13px; }}
#profileName {{ font-size: 13px; font-weight: 650; }}
#status, #online {{ color: #20A05A; font-size: 10px; }}
#status[connected="false"] {{ color: #D44D59; }}
#chatPanel, #conversation {{ background: white; }}
#chatPanel {{ border-bottom-right-radius: 23px; }}
#learningToggle {{
    color: #526176; font-size: 11px; font-weight: 650; spacing: 7px;
}}
#learningToggle::indicator {{
    width: 32px; height: 17px; border-radius: 9px;
    border: 1px solid #B9C8D9; background: #E7EDF4;
}}
#learningToggle::indicator:checked {{
    border: 1px solid #229A68; background: #35B77D;
}}
#learningBanner {{
    background: #DDF8EA; color: #12633F; border-bottom: 1px solid #A9E2C6;
    padding: 9px 14px; font-size: 11px; font-weight: 700;
}}
#loadMoreButton {{
    background: #EDF4FD; color: {BLUE}; border: 1px solid #CFE1F7;
    border-radius: 14px; padding: 6px 14px; font-size: 11px; font-weight: 650;
    margin-bottom: 8px;
}}
#loadMoreButton:hover {{ background: #DDECFD; }}
#loadMoreButton:disabled {{ color: #8796A9; background: #F3F6F9; }}
#jumpToBottomButton {{
    background: white; border: 1px solid #C8D8EA; border-radius: 17px;
    padding: 0;
}}
#jumpToBottomButton:hover {{ background: #EDF4FD; border-color: #AFC9E8; }}
#chatScroll {{ border: none; background: white; }}
#bubbleIncoming, #bubbleOutgoing {{
    border: none; border-radius: 16px; padding: 11px 14px;
    font-size: 13px; line-height: 1.35;
}}
#bubbleIncoming {{ background: {PALE_BLUE}; color: {DARK}; }}
#bubbleOutgoing {{ background: {BLUE}; color: white; }}
#typingBubble {{
    background: {PALE_BLUE}; border-radius: 13px; min-width: 38px;
}}
#typingDots {{ color: {BLUE}; font-size: 15px; font-weight: 700; }}
#typingSummary {{ color: {MUTED}; font-size: 12px; font-weight: 650; }}
#timestamp {{ color: {MUTED}; font-size: 10px; }}
#emptyTitle {{ color: #53647B; font-size: 14px; font-weight: 650; }}
#emptyText {{ color: #95A4B8; font-size: 11px; }}
#emptyIcon {{ margin-bottom: 3px; }}
#composer {{
    background: white; border: 1px solid #D8E4F1; border-radius: 22px;
}}
#messageInput {{
    border: none; background: transparent; font-size: 13px; padding: 4px;
}}
#messageInput::placeholder {{ color: #91A0B5; }}
#attachButton {{
    background: transparent; border: none; color: {BLUE};
    font-size: 20px; width: 28px; height: 34px;
}}
#sendButton {{
    background: {BLUE}; border: none; color: white; font-size: 19px;
    width: 40px; height: 40px; border-radius: 20px;
}}
#sendButton:hover {{ background: #1768D5; }}
#stopStatus {{
    color: {MUTED}; font-size: 9px; font-weight: 600;
}}
#loginPage {{
    background: #F7FAFE;
    border-bottom-left-radius: 23px;
    border-bottom-right-radius: 23px;
}}
#loginCard {{
    background: white; border: 1px solid #D9E5F2; border-radius: 22px;
}}
#loginTitle {{ font-size: 23px; font-weight: 700; color: {DARK}; }}
#loginSubtitle {{ color: {MUTED}; font-size: 12px; }}
#fieldLabel {{ color: #3C4C63; font-size: 12px; font-weight: 600; }}
#loginInput {{
    background: #F8FAFD; border: 1px solid #D8E3F0; border-radius: 11px;
    padding: 0 12px; min-height: 43px; font-size: 13px;
}}
#loginInput:focus {{ background: white; border: 1px solid {BLUE}; }}
#rememberCheck {{ color: {MUTED}; font-size: 11px; }}
#textButton {{
    background: transparent; border: none; color: {BLUE}; font-size: 11px;
}}
#textButton:hover {{ color: #145FC5; text-decoration: underline; }}
#loginButton {{
    background: {BLUE}; color: white; border: none; border-radius: 11px;
    min-height: 44px; font-size: 14px; font-weight: 650;
}}
#loginButton:hover {{ background: #1768D5; }}
#loginButton:pressed {{ background: #1159BB; }}
#loginError {{
    color: #C3424D; background: #FFF1F2; border: 1px solid #FFD8DC;
    border-radius: 8px; padding: 7px; font-size: 10px;
}}
#secureLabel {{ color: #38A66B; font-size: 10px; }}
#jobsPanel, #jobsPage, #jobListContent, #filesPanel, #filesPage, #settingsPanel, #settingsPage {{ background: white; }}
#jobsPage, #filesPage, #settingsPage {{
    border-bottom-left-radius: 23px;
    border-bottom-right-radius: 23px;
}}
#jobsPanel, #filesPanel, #settingsPanel {{ border-bottom-right-radius: 23px; }}
#filesPrimary, #filesSecondary {{
    min-height: 38px; border-radius: 10px; padding: 0 13px;
    font-size: 12px; font-weight: 650;
}}
#filesPrimary {{ background: {BLUE}; color: white; border: none; }}
#filesPrimary:hover {{ background: #1768D5; }}
#filesSecondary {{ background: white; color: {BLUE}; border: 1px solid #C8D8EA; }}
#filesSecondary:hover {{ background: {PALE_BLUE}; border-color: #AFC9E8; }}
#filesBack {{
    background: transparent; border: none; border-radius: 9px;
    width: 34px; height: 34px;
}}
#filesBack:hover {{ background: #EDF4FD; }}
#filesBack:disabled {{ background: transparent; }}
#filesLocation {{
    background: #F8FAFD; border: 1px solid #D8E3F0; border-radius: 9px;
    color: #526176; padding: 7px 11px; font-size: 11px;
}}
#fileTree {{
    background: white; alternate-background-color: #F8FAFD;
    border: 1px solid #D8E3F0; border-radius: 11px;
    font-size: 11px; outline: none;
}}
#fileTree::item {{ min-height: 34px; padding: 3px; }}
#fileTree::item:selected {{ background: #DCEBFD; color: {DARK}; }}
#fileTree QHeaderView::section {{
    background: #F3F7FC; color: #60728A; border: none;
    border-bottom: 1px solid #D8E3F0; padding: 8px; font-size: 10px; font-weight: 650;
}}
#filesHint {{ color: #8796A9; font-size: 10px; padding: 4px; }}
#sectionTitle {{ font-size: 22px; font-weight: 700; color: {DARK}; }}
#sectionSubtitle {{ color: {MUTED}; font-size: 12px; }}
#jobTab, #jobTabActive {{
    border: none; border-radius: 10px; padding: 8px 14px;
    font-size: 12px; font-weight: 600;
}}
#jobTab {{ background: #EEF3F9; color: #67778E; }}
#jobTab:hover {{ background: #E3ECF7; color: {BLUE}; }}
#jobTabActive {{ background: {BLUE}; color: white; }}
#jobListScroll, #jobDetailScroll {{ border: none; background: white; }}
#jobRow {{
    background: #F9FBFE; border: 1px solid #DDE8F4; border-radius: 14px;
}}
#jobRow:hover {{ background: #F0F6FE; border: 1px solid #BCD5F3; }}
#jobIcon {{ background: #E6F1FF; border-radius: 11px; }}
#jobTitle {{ color: {DARK}; font-size: 13px; font-weight: 650; }}
#jobMeta {{ color: {MUTED}; font-size: 10px; }}
#jobStatusActive, #jobStatusSuccess, #jobStatusFailed, #jobStatusPending,
#jobStatusProcessing, #jobStatusInactive {{
    border-radius: 8px; padding: 4px 7px; font-size: 9px; font-weight: 650;
}}
#jobStatusActive {{ background: #E6F1FF; color: {BLUE}; }}
#jobStatusSuccess {{ background: #E6F8EE; color: #239A5A; }}
#jobStatusFailed {{ background: #FFF0F1; color: #D44D59; }}
#jobStatusPending {{ background: #FFF6E2; color: #B87A16; }}
#jobStatusProcessing {{ background: #EAF3FF; color: {BLUE}; }}
#jobStatusInactive {{ background: #EDF1F5; color: #68788E; }}
#backButton {{
    background: transparent; border: none; color: {BLUE};
    font-size: 11px; font-weight: 600; text-align: left;
}}
#backButton:hover {{ color: #155FCA; }}
#jobDetailTitle {{ font-size: 20px; font-weight: 700; color: {DARK}; }}
#jobDetailId {{ color: {MUTED}; font-size: 10px; }}
#jobField {{
    background: #F8FAFD; border: 1px solid #E0E9F3; border-radius: 11px;
}}
#jobFieldName {{ color: #6C7D93; font-size: 9px; font-weight: 700; }}
#jobFieldValue {{ color: #26364C; font-size: 11px; }}
#jobProgressCard {{
    background: #F2F7FD; border: 1px solid #D8E7F7; border-radius: 14px;
}}
#jobProgressTitle {{ color: #8190A5; font-size: 11px; font-weight: 600; }}
#jobWorkedFor {{ color: #60728A; font-size: 10px; font-weight: 650; }}
#jobProgressLabel {{ color: #60728A; font-size: 10px; font-weight: 700; }}
#jobProgressText {{
    background: #E5F0FC; color: #405B7B; border-radius: 10px;
    padding: 10px 12px; font-size: 12px;
}}
#settingsInput {{
    background: #F8FAFD; border: 1px solid #D8E3F0; border-radius: 11px;
    padding: 0 12px; min-height: 43px; font-size: 13px;
}}
#settingsInput:focus {{ background: white; border: 1px solid {BLUE}; }}
#settingsHelp {{ color: {MUTED}; font-size: 10px; }}
#settingsSectionTitle {{ color: {DARK}; font-size: 13px; font-weight: 700; }}
#settingsSave {{
    background: {BLUE}; color: white; border: none; border-radius: 11px;
    min-height: 42px; font-size: 13px; font-weight: 650;
}}
#settingsSave:hover {{ background: #1768D5; }}
#settingsSecondary {{
    background: white; color: {BLUE}; border: 1px solid {BLUE}; border-radius: 11px;
    min-height: 42px; font-size: 13px; font-weight: 650;
}}
#settingsSecondary:hover {{ background: {PALE_BLUE}; }}
#connectionLogOutput {{
    background: #101827; color: #DCE7F4; border: 1px solid #293850;
    border-radius: 10px; padding: 10px; font-family: monospace; font-size: 11px;
}}
#settingsFeedback {{ color: #239A5A; font-size: 11px; padding: 6px 0; }}
#settingsFeedback[error="true"] {{ color: #C3424D; }}
#swarifWindow[learningMode="true"] #windowCard {{
    background: rgba(235, 252, 243, 235); border: 1px solid #9EDABB;
}}
#swarifWindow[learningMode="true"] #titleBar,
#swarifWindow[learningMode="true"] #sidebar {{
    background: rgba(225, 248, 237, 225); border-color: #A9DFC4;
}}
#swarifWindow[learningMode="true"] #chatPanel,
#swarifWindow[learningMode="true"] #conversation,
#swarifWindow[learningMode="true"] #chatScroll,
#swarifWindow[learningMode="true"] #homePage,
#swarifWindow[learningMode="true"] #jobsPanel,
#swarifWindow[learningMode="true"] #jobsPage,
#swarifWindow[learningMode="true"] #filesPanel,
#swarifWindow[learningMode="true"] #filesPage,
#swarifWindow[learningMode="true"] #settingsPanel,
#swarifWindow[learningMode="true"] #settingsPage {{
    background: rgba(242, 253, 247, 225); border-color: #B8E5CE;
}}
#swarifWindow[learningMode="true"] #navActive,
#swarifWindow[learningMode="true"] #settingsIconActive,
#swarifWindow[learningMode="true"] #sendButton,
#swarifWindow[learningMode="true"] #settingsSave {{ background: #249B69; }}
#swarifWindow[learningMode="true"] #brand,
#swarifWindow[learningMode="true"] #typingDots {{ color: #20865D; }}
#swarifWindow[learningMode="true"] #bubbleIncoming {{
    background: rgba(214, 246, 229, 220); color: #164B37;
}}
#swarifWindow[compact="true"] #windowCard {{
    background: white; border: 1px solid #C9D9EA; border-radius: 34px;
}}
#swarifWindow[compact="true"] #titleBar {{
    background: white; border: none; border-radius: 34px;
}}

/* Deep-blue application theme. Popups intentionally remain light. */
#swarifWindow #windowCard {{ background: #07182F; border-color: #284B73; }}
#swarifWindow #titleBar {{ background: #0C2344; border-bottom-color: #284B73; }}
#swarifWindow #brand {{ color: #F3F7FC; }}
#swarifWindow #windowButton {{ color: #C5D5E7; }}
#swarifWindow #windowButton:hover {{ background: #193B65; color: white; }}
#swarifWindow #sidebar {{ background: #0A1F3B; border-right-color: #284B73; }}
#swarifWindow #navButton, #swarifWindow #logoutButton {{ color: #C5D5E7; }}
#swarifWindow #navButton:hover, #swarifWindow #settingsIconButton:hover {{ background: #17375F; color: white; }}
#swarifWindow #navActive, #swarifWindow #settingsIconActive {{ background: #2478EB; color: white; }}
#swarifWindow #logoutButton:hover {{ background: #4A2338; color: #FFABB5; }}
#swarifWindow #profile {{ background: #132F53; }}
#swarifWindow #profileName {{ color: #F3F7FC; }}

#swarifWindow #loginPage, #swarifWindow #homePage,
#swarifWindow #jobsPage, #swarifWindow #filesPage, #swarifWindow #settingsPage,
#swarifWindow #chatPanel, #swarifWindow #conversation,
#swarifWindow #jobsPanel, #swarifWindow #jobListContent,
#swarifWindow #filesPanel, #swarifWindow #settingsPanel {{ background: #0C2344; }}
#swarifWindow #loginCard {{ background: #102B50; border-color: #31577F; }}
#swarifWindow #loginTitle, #swarifWindow #sectionTitle,
#swarifWindow #settingsSectionTitle, #swarifWindow #fieldLabel,
#swarifWindow #assistantTitle, #swarifWindow #jobDetailTitle {{ color: #F3F7FC; }}
#swarifWindow #loginSubtitle, #swarifWindow #sectionSubtitle,
#swarifWindow #settingsHelp, #swarifWindow #timestamp,
#swarifWindow #typingSummary, #swarifWindow #stopStatus,
#swarifWindow #jobMeta, #swarifWindow #jobDetailId,
#swarifWindow #filesHint {{ color: #A9BCD2; }}

#swarifWindow #loginInput, #swarifWindow #settingsInput,
#swarifWindow #filesLocation, #swarifWindow #jobField {{
    background: #102B50; color: #F3F7FC; border-color: #31577F;
}}
#swarifWindow #loginInput:focus, #swarifWindow #settingsInput:focus {{
    background: #132F53; border-color: #5598F5;
}}
#swarifWindow #rememberCheck, #swarifWindow #learningToggle {{ color: #C5D5E7; }}
#swarifWindow #textButton, #swarifWindow #filesLocation {{ color: #77ACFA; }}

#swarifWindow #chatScroll, #swarifWindow #jobListScroll,
#swarifWindow #jobDetailScroll {{ background: #0C2344; border-color: #284B73; }}
#swarifWindow #composer {{ background: #102B50; border-color: #31577F; }}
#swarifWindow #messageInput {{ background: transparent; color: #F3F7FC; }}
#swarifWindow #bubbleIncoming, #swarifWindow #typingBubble {{ background: #17375F; color: #F3F7FC; }}
#swarifWindow #bubbleOutgoing {{ background: #2478EB; color: white; }}
#swarifWindow #loadMoreButton, #swarifWindow #jumpToBottomButton {{
    background: #15365F; color: #8DBBFF; border-color: #31577F;
}}
#swarifWindow #loadMoreButton:hover, #swarifWindow #jumpToBottomButton:hover {{ background: #1B4778; }}
#swarifWindow #emptyTitle {{ color: #DCE8F5; }}
#swarifWindow #emptyText {{ color: #8FA7C1; }}

#swarifWindow #fileTree {{
    background: #0F294B; alternate-background-color: #122F54;
    color: #E8F0F8; border-color: #31577F;
}}
#swarifWindow #fileTree::item:selected {{ background: #22558D; color: white; }}
#swarifWindow #fileTree QHeaderView::section {{
    background: #102B50; color: #B8C9DC; border-bottom-color: #31577F;
}}
#swarifWindow #filesSecondary, #swarifWindow #settingsSecondary {{
    background: #102B50; color: #77ACFA; border-color: #3C6795;
}}
#swarifWindow #filesSecondary:hover, #swarifWindow #settingsSecondary:hover {{ background: #17375F; }}
#swarifWindow #filesBack:hover {{ background: #17375F; }}

#swarifWindow #jobRow {{ background: #102B50; border-color: #294E76; }}
#swarifWindow #jobRow:hover {{ background: #15365F; border-color: #477DB2; }}
#swarifWindow #jobTitle, #swarifWindow #jobFieldValue {{ color: #EAF1F8; }}
#swarifWindow #jobFieldName {{ color: #9CB2C9; }}
#swarifWindow #jobProgressCard {{ background: #102B50; border-color: #31577F; }}
#swarifWindow #jobProgressText {{ background: #17375F; color: #DCE8F5; }}
#swarifWindow #jobProgressTitle, #swarifWindow #jobProgressLabel,
#swarifWindow #jobWorkedFor {{ color: #A9BCD2; }}
#swarifWindow #jobTab {{ background: #15365F; color: #AFC3D8; }}
#swarifWindow #jobTab:hover {{ background: #1B4778; color: white; }}

#swarifWindow[compact="true"] #windowCard,
#swarifWindow[compact="true"] #titleBar {{ background: #0C2344; border-color: #31577F; }}

/* Predominantly white brand theme with deep-blue navigation. */
#swarifWindow #windowCard {{ background: white; border-color: #C9D9EA; }}
#swarifWindow #titleBar {{ background: #0C2344; border-bottom-color: #27496E; }}
#swarifWindow #sidebar {{ background: #0C2344; border-right-color: #27496E; }}
#swarifWindow #profile {{ background: #17375F; }}

#swarifWindow #loginPage, #swarifWindow #homePage,
#swarifWindow #jobsPage, #swarifWindow #filesPage, #swarifWindow #settingsPage,
#swarifWindow #chatPanel, #swarifWindow #conversation,
#swarifWindow #jobsPanel, #swarifWindow #jobListContent,
#swarifWindow #filesPanel, #swarifWindow #settingsPanel {{ background: white; }}
#swarifWindow #loginPage {{ background: #F4F8FE; }}
#swarifWindow #loginCard {{ background: white; border-color: #C9D9EA; }}
#swarifWindow #loginTitle, #swarifWindow #sectionTitle,
#swarifWindow #settingsSectionTitle, #swarifWindow #fieldLabel,
#swarifWindow #assistantTitle, #swarifWindow #jobDetailTitle {{ color: #0C2344; }}
#swarifWindow #loginSubtitle, #swarifWindow #sectionSubtitle,
#swarifWindow #settingsHelp, #swarifWindow #timestamp,
#swarifWindow #typingSummary, #swarifWindow #stopStatus,
#swarifWindow #jobMeta, #swarifWindow #jobDetailId,
#swarifWindow #filesHint {{ color: #6F829B; }}

#swarifWindow #loginInput, #swarifWindow #settingsInput,
#swarifWindow #filesLocation, #swarifWindow #jobField {{
    background: #F5F9FE; color: #15233A; border-color: #C9D9EA;
}}
#swarifWindow #loginInput:focus, #swarifWindow #settingsInput:focus {{
    background: white; border-color: #2478EB;
}}
#swarifWindow #rememberCheck, #swarifWindow #learningToggle {{ color: #526176; }}
#swarifWindow #textButton, #swarifWindow #filesLocation {{ color: #2478EB; }}

#swarifWindow #chatScroll, #swarifWindow #jobListScroll,
#swarifWindow #jobDetailScroll {{ background: white; border-color: #D8E3F0; }}
#swarifWindow #composer {{ background: white; border-color: #C9D9EA; }}
#swarifWindow #messageInput {{ background: transparent; color: #15233A; }}
#swarifWindow #bubbleIncoming, #swarifWindow #typingBubble {{ background: #EAF3FF; color: #15233A; }}
#swarifWindow #bubbleOutgoing {{ background: #2478EB; color: white; }}
#swarifWindow #loadMoreButton, #swarifWindow #jumpToBottomButton {{
    background: #F1F6FD; color: #2478EB; border-color: #C9D9EA;
}}
#swarifWindow #loadMoreButton:hover, #swarifWindow #jumpToBottomButton:hover {{ background: #E1EDFB; }}
#swarifWindow #emptyTitle {{ color: #405572; }}
#swarifWindow #emptyText {{ color: #8798AC; }}

#swarifWindow #fileTree {{
    background: white; alternate-background-color: #F5F9FE;
    color: #15233A; border-color: #C9D9EA;
}}
#swarifWindow #fileTree::item:selected {{ background: #DCEBFD; color: #0C2344; }}
#swarifWindow #fileTree QHeaderView::section {{
    background: #EAF3FF; color: #405572; border-bottom-color: #C9D9EA;
}}
#swarifWindow #filesSecondary, #swarifWindow #settingsSecondary {{
    background: white; color: #2478EB; border-color: #8CB7EA;
}}
#swarifWindow #filesSecondary:hover, #swarifWindow #settingsSecondary:hover {{ background: #EAF3FF; }}
#swarifWindow #filesBack:hover {{ background: #EAF3FF; }}

#swarifWindow #jobRow {{ background: #F7FAFE; border-color: #D6E3F1; }}
#swarifWindow #jobRow:hover {{ background: #EAF3FF; border-color: #AFC9E8; }}
#swarifWindow #jobTitle, #swarifWindow #jobFieldValue {{ color: #15233A; }}
#swarifWindow #jobFieldName {{ color: #6F829B; }}
#swarifWindow #jobProgressCard {{ background: #F1F6FD; border-color: #D3E2F2; }}
#swarifWindow #jobProgressText {{ background: #E3EFFC; color: #405572; }}
#swarifWindow #jobProgressTitle, #swarifWindow #jobProgressLabel,
#swarifWindow #jobWorkedFor {{ color: #60728A; }}
#swarifWindow #jobTab {{ background: #EAF1F9; color: #60728A; }}
#swarifWindow #jobTab:hover {{ background: #DCEBFD; color: #2478EB; }}

#swarifWindow[compact="true"] #windowCard,
#swarifWindow[compact="true"] #titleBar {{ background: #0C2344; border-color: #27496E; }}

/* Restore the original light chrome; only the Swarif wordmark is deep blue. */
#swarifWindow #windowCard {{ background: #F9FBFE; border-color: #D7E3F1; }}
#swarifWindow #titleBar {{ background: #F7FAFE; border-bottom-color: {BORDER}; }}
#swarifWindow #brand {{ color: #0C2344; }}
#swarifWindow #windowButton {{ color: #526176; }}
#swarifWindow #windowButton:hover {{ background: #E9F1FB; color: #17243A; }}
#swarifWindow #sidebar {{ background: #F7FAFE; border-right-color: {BORDER}; }}
#swarifWindow #navButton {{ background: transparent; color: #40516A; }}
#swarifWindow #navButton:hover, #swarifWindow #settingsIconButton:hover {{
    background: #EDF4FD; color: #2478EB;
}}
#swarifWindow #logoutButton {{ background: transparent; color: #526176; }}
#swarifWindow #logoutButton:hover {{ background: #FFF0F1; color: #D84B57; }}
#swarifWindow #profile {{ background: #EEF5FE; }}
#swarifWindow #profileName {{ color: #15233A; }}
#swarifWindow[compact="true"] #windowCard,
#swarifWindow[compact="true"] #titleBar {{ background: white; border-color: #C9D9EA; }}

/* Soft blue glass composer. */
#swarifWindow #composerWrap {{
    background: transparent;
    border: none;
}}
#swarifWindow #composer {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 rgba(246, 251, 255, 238),
        stop: 0.52 rgba(224, 241, 255, 214),
        stop: 1 rgba(239, 248, 255, 230)
    );
    border: 1px solid #B7D8FB;
    border-radius: 28px;
}}
#swarifWindow #messageInput {{
    background: transparent;
    color: #17375F;
    border: none;
    padding: 5px 4px;
    font-size: 13px;
}}
#swarifWindow #messageInput::placeholder {{ color: #829ABD; }}
#swarifWindow #attachButton {{
    background: transparent;
    border: none;
    border-radius: 17px;
}}
#swarifWindow #attachButton:hover {{ background: rgba(192, 222, 252, 145); }}
#swarifWindow #sendButton {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #369BFF,
        stop: 0.48 #1684F5,
        stop: 1 #086DE0
    );
    border: 1px solid rgba(83, 167, 255, 180);
    border-radius: 20px;
}}
#swarifWindow #sendButton:hover {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 1, y2: 1,
        stop: 0 #55AAFF,
        stop: 1 #147BE8
    );
}}

/* Raised, dimensional chat cards. */
#swarifWindow #bubbleIncoming {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #F5FAFF,
        stop: 0.55 #EAF3FF,
        stop: 1 #DDECFD
    );
    color: #15233A;
    border: 1px solid #C5DCF5;
    border-radius: 16px;
}}
#swarifWindow #bubbleOutgoing {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #3A91F3,
        stop: 0.5 #2478EB,
        stop: 1 #1768D5
    );
    color: white;
    border: 1px solid #579DF0;
    border-radius: 16px;
}}

/* Raised selected controls. */
#swarifWindow #navActive, #swarifWindow #settingsIconActive,
#swarifWindow #jobTabActive {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #469AF5,
        stop: 0.5 #2478EB,
        stop: 1 #1768D5
    );
    color: white;
    border: 1px solid #62A6F2;
}}
#swarifWindow #navActive:hover, #swarifWindow #settingsIconActive:hover,
#swarifWindow #jobTabActive:hover {{
    background: qlineargradient(
        x1: 0, y1: 0, x2: 0, y2: 1,
        stop: 0 #57A6FA,
        stop: 1 #1C72DD
    );
}}
QScrollBar:vertical {{ background: transparent; width: 5px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: #46698F; border-radius: 2px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Swarif")
    app.setWindowIcon(QIcon(str(static_asset("Swarif_Logo_BG.png"))))
    app.setFont(QFont("Arial", 10))
    window = SwarifWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
