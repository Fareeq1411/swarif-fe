import json
import ipaddress
import os
import platform
import socket
import subprocess
import sys
from datetime import datetime

from PyQt5.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QStackedWidget,
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


class BotAvatar(QWidget):
    """Small code-drawn avatar so the prototype needs no image assets."""

    def __init__(self, size=38, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#E4F0FF"))
        painter.drawEllipse(0, 0, side, side)

        margin = side * 0.22
        body = QRectF(margin, side * 0.26, side - margin * 2, side * 0.52)
        painter.setBrush(QColor(BLUE))
        painter.drawRoundedRect(body, side * 0.16, side * 0.16)

        painter.setBrush(QColor("white"))
        eye = side * 0.09
        painter.drawEllipse(QRectF(side * 0.34, side * 0.43, eye, eye))
        painter.drawEllipse(QRectF(side * 0.57, side * 0.43, eye, eye))

        painter.setPen(QPen(QColor("white"), max(1.4, side * 0.04)))
        painter.drawArc(
            QRectF(side * 0.40, side * 0.49, side * 0.21, side * 0.17),
            200 * 16,
            140 * 16,
        )


class BrandMark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(39, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(QPen(QColor(BLUE), 2.8, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        path = QPainterPath()
        path.moveTo(9, 22)
        path.cubicTo(2, 22, 2, 12, 10, 11)
        path.cubicTo(12, 2, 26, 1, 29, 11)
        path.cubicTo(39, 11, 39, 22, 31, 22)
        path.closeSubpath()
        painter.drawPath(path)


class TitleBar(QFrame):
    minimize_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(68)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(21, 0, 14, 0)
        layout.setSpacing(8)

        layout.addWidget(BrandMark())
        self.brand = QLabel("swarif")
        self.brand.setObjectName("brand")
        layout.addWidget(self.brand)
        layout.addStretch()

        self.minimize = QPushButton()
        self.minimize.setObjectName("windowButton")
        self.minimize.setIcon(qta.icon("fa5s.window-minimize", color="#526176"))
        self.minimize.setIconSize(QSize(13, 13))
        self.minimize.setToolTip("Minimize to floating icon")
        self.minimize.clicked.connect(self.minimize_clicked)
        close = QPushButton()
        close.setObjectName("windowButton")
        close.setIcon(qta.icon("fa5s.times", color="#526176"))
        close.setIconSize(QSize(15, 15))
        close.setToolTip("Close")
        close.clicked.connect(self.close_clicked)
        layout.addWidget(self.minimize)
        layout.addWidget(close)

    def set_compact(self, compact):
        self.brand.setVisible(not compact)
        icon_name = "fa5s.expand-alt" if compact else "fa5s.window-minimize"
        self.minimize.setIcon(qta.icon(icon_name, color="#526176"))
        self.minimize.setToolTip("Restore Swarif" if compact else "Minimize to floating icon")

class NavButton(QPushButton):
    def __init__(self, icon_name, text, active=False, parent=None):
        super().__init__(text, parent)
        self.setObjectName("navActive" if active else "navButton")
        self.setIcon(qta.icon(icon_name, color="white" if active else "#40516A"))
        self.setIconSize(QSize(17, 17))
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(46)


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

        chat_button = NavButton("fa5s.th-large", "Chat", active=active_page == "chat")
        jobs_button = NavButton("fa5s.briefcase", "Jobs", active=active_page == "jobs")
        chat_button.clicked.connect(self.chat_clicked)
        jobs_button.clicked.connect(self.jobs_clicked)
        layout.addWidget(chat_button)
        layout.addWidget(jobs_button)
        files_button = NavButton("fa5s.folder-open", "Files", active=active_page == "files")
        settings_button = NavButton("fa5s.cog", "Settings", active=active_page == "settings")
        files_button.clicked.connect(self.files_clicked)
        settings_button.clicked.connect(self.settings_clicked)
        layout.addWidget(files_button)
        layout.addWidget(settings_button)
        layout.addStretch()

        profile = QFrame()
        profile.setObjectName("profile")
        profile_layout = QHBoxLayout(profile)
        profile_layout.setContentsMargins(5, 8, 3, 8)
        profile_layout.setSpacing(8)
        profile_layout.addWidget(BotAvatar(34))

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
        layout.addWidget(logout)
        self.set_connected(connected)

    def set_connected(self, connected):
        self.status.setText("●  Connected" if connected else "●  Disconnected")
        self.status.setProperty("connected", bool(connected))
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)


class MessageBubble(QWidget):
    def __init__(self, text, timestamp, outgoing=False, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 6)
        outer.setSpacing(4)

        line = QHBoxLayout()
        line.setSpacing(8)
        if outgoing:
            line.addStretch()
        else:
            line.addWidget(BotAvatar(32), 0, Qt.AlignTop)

        bubble = QLabel(text)
        bubble.setWordWrap(True)
        bubble.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bubble.setObjectName("bubbleOutgoing" if outgoing else "bubbleIncoming")
        bubble.setMaximumWidth(255)
        bubble.setMinimumWidth(235 if outgoing else 220)
        bubble.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
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
    def __init__(self, parent=None):
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
        bubble_layout.addWidget(AnimatedDots())
        layout.addWidget(bubble, 0, Qt.AlignTop)
        layout.addStretch()


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


class GrowingMessageEdit(QTextEdit):
    submit_requested = pyqtSignal()

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
        self.setFixedHeight(max(34, min(target, 180)))

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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("composer")
        self.setMinimumHeight(58)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(13, 6, 8, 6)
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
        send = QPushButton()
        send.setObjectName("sendButton")
        send.setIcon(qta.icon("fa5s.paper-plane", color="white"))
        send.setIconSize(QSize(18, 18))
        send.setToolTip("Send (Shift+Enter)")
        send.clicked.connect(self.submit_message)

        layout.addWidget(attach)
        layout.addWidget(self.message, 1)
        layout.addWidget(send)

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
    def __init__(self, job, current_progress=None, parent=None):
        super().__init__(parent)
        self.setObjectName("jobProgressCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(13, 10, 13, 12)
        layout.setSpacing(6)
        title = QLabel(f'Job title: "{job.get("title") or "Untitled job"}" is processing')
        title.setObjectName("jobProgressTitle")
        title.setWordWrap(True)
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
        layout.addWidget(title)
        layout.addLayout(label_row)
        layout.addWidget(progress)


class ChatPanel(QFrame):
    message_submitted = pyqtSignal(str)

    def __init__(self, chat_messages=None, newest_first=False, processing_jobs=None, job_progress=None, agent_typing=False, parent=None):
        super().__init__(parent)
        self.setObjectName("chatPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("chatHeader")
        header.setFixedHeight(76)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(19, 11, 18, 11)
        header_layout.setSpacing(11)
        header_layout.addWidget(BotAvatar(44))

        title = QLabel("Swarif")
        title.setObjectName("assistantTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setObjectName("chatScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        conversation = QWidget()
        conversation.setObjectName("conversation")
        messages = QVBoxLayout(conversation)
        messages.setContentsMargins(18, 20, 18, 8)
        messages.setSpacing(2)
        valid_messages = [item for item in (chat_messages or []) if isinstance(item, dict)]
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

        self.typing_indicator = TypingIndicator()
        self.typing_indicator.setVisible(bool(agent_typing))
        messages.addWidget(self.typing_indicator)

        self.processing_container = QWidget()
        self.processing_layout = QVBoxLayout(self.processing_container)
        self.processing_layout.setContentsMargins(0, 8, 0, 8)
        self.processing_layout.setSpacing(9)
        messages.addWidget(self.processing_container)
        messages.addItem(QSpacerItem(1, 1, QSizePolicy.Minimum, QSizePolicy.Expanding))
        self.update_processing_jobs(processing_jobs or [], job_progress or {})
        scroll.setWidget(conversation)
        layout.addWidget(scroll, 1)
        self.chat_scroll = scroll
        scroll.verticalScrollBar().rangeChanged.connect(
            lambda _minimum, maximum: scroll.verticalScrollBar().setValue(maximum)
        )
        QTimer.singleShot(0, self.scroll_to_bottom)
        QTimer.singleShot(100, self.scroll_to_bottom)

        composer_wrap = QWidget()
        composer_layout = QVBoxLayout(composer_wrap)
        composer_layout.setContentsMargins(15, 10, 15, 16)
        composer = Composer()
        composer.message_submitted.connect(self.message_submitted)
        composer_layout.addWidget(composer)
        layout.addWidget(composer_wrap)

    def scroll_to_bottom(self):
        scrollbar = self.chat_scroll.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def set_agent_typing(self, typing):
        self.typing_indicator.setVisible(bool(typing))
        if typing:
            QTimer.singleShot(0, self.scroll_to_bottom)

    def update_processing_jobs(self, processing_jobs, job_progress):
        while self.processing_layout.count():
            item = self.processing_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for job in processing_jobs:
            self.processing_layout.addWidget(
                JobProgressBubble(job, job_progress.get(job.get("id")))
            )
        self.processing_container.setVisible(bool(processing_jobs))
        if self.empty_state is not None:
            self.empty_state.setVisible(not processing_jobs)
        QTimer.singleShot(0, self.scroll_to_bottom)


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
    saved = pyqtSignal(str, int)
    connection_log_requested = pyqtSignal()

    def __init__(self, agent_ip="", server_port=8767, parent=None):
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

        label = QLabel("Agent IP address")
        label.setObjectName("fieldLabel")
        self.agent_ip = QLineEdit(agent_ip)
        self.agent_ip.setObjectName("settingsInput")
        self.agent_ip.setPlaceholderText("e.g. 192.168.1.50")
        self.agent_ip.returnPressed.connect(self.save)
        port_label = QLabel("Agent port")
        port_label.setObjectName("fieldLabel")
        self.server_port = QLineEdit(str(server_port or 8767))
        self.server_port.setObjectName("settingsInput")
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

    def save(self):
        value = self.agent_ip.text().strip()
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
        self.saved.emit(value, port)

    def show_saved(self):
        self.feedback.setText("Agent connection settings saved.")
        self.feedback.setProperty("error", False)
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        self.feedback.show()

    def show_local_llm_placeholder(self):
        self.local_llm_feedback.setText("Local LLM setup will be available in a future update.")
        self.local_llm_feedback.show()


class SwarifWindow(QWidget):
    message_submitted = pyqtSignal(str)
    server_connection_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self._anchoring = False
        self._server_connected = False
        self.sidebar = None
        self._job_progress = {}
        self._active_jobs = []
        self._connection_log = []
        self._connection_log_dialog = None
        self._agent_typing = False
        self._inactive_jobs = []
        self.jobs_panel = None
        self._compact = False
        self._expanded_size = QSize(760, 700)
        self.setWindowTitle("Swarif")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(760, 700)
        self.setMinimumSize(500, 520)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(0)

        card = QFrame()
        card.setObjectName("windowCard")
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
        """Collapse to a bottom-right floating control or restore the window."""
        self._compact = not self._compact
        if self._compact:
            self._expanded_size = self.size()
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
        self.show()
        QTimer.singleShot(0, self.anchor_bottom_right)

    def show_login(self):
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

    def show_home(self):
        session = Agent.read_session()
        if not session:
            self.show_login()
            return

        if not isinstance(session.get("name"), str) or not session["name"].strip():
            try:
                refreshed_session = Agent.refresh_session_user()
                if refreshed_session:
                    session = refreshed_session
            except (RuntimeError, ConnectionError) as error:
                log(error)
                pass

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
        try:
            chat_messages = Agent.sync_chat_from_database()
        except (ValueError, RuntimeError, ConnectionError) as error:
            log(error)
            try:
                chat_messages = Agent.read_local_chat()
            except RuntimeError as local_error:
                log(local_error)
                chat_messages = []
        body_layout.addWidget(sidebar)
        self.home_layout = body_layout
        self.chat_panel = ChatPanel(
            chat_messages=chat_messages or [],
            newest_first=False,
            processing_jobs=self.processing_jobs(),
            job_progress=self._job_progress,
            agent_typing=self._agent_typing,
        )
        self.chat_panel.message_submitted.connect(self.message_submitted)
        body_layout.addWidget(self.chat_panel, 1)
        self.set_page(body)

    def show_jobs(self):
        session = Agent.read_session()
        if not session:
            self.show_login()
            return

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
        settings = SettingsPanel(
            session.get("agent_ip", ""),
            session.get("server_port", 8767),
        )
        settings.saved.connect(
            lambda agent_ip, server_port: self.save_agent_connection(
                agent_ip, server_port, settings
            )
        )
        settings.connection_log_requested.connect(self.show_connection_log)
        body_layout.addWidget(settings, 1)
        self.set_page(body)

    def append_connection_log(self, message):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] {message}"
        self._connection_log.append(entry)
        self._connection_log = self._connection_log[-1000:]
        if self._connection_log_dialog is not None:
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

    def save_agent_connection(self, agent_ip, server_port, panel):
        try:
            Agent.update_session(
                {"agent_ip": agent_ip, "server_port": server_port}
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

    def set_agent_typing(self, typing):
        self._agent_typing = bool(typing)
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.chat_panel.set_agent_typing(self._agent_typing)

    def update_job_progress(self, job_id, current_progress):
        """Keep and display the newest TCP progress value for a job."""
        self._job_progress[job_id] = current_progress
        if (
            self.current_page is not None
            and self.current_page.objectName() == "jobsPage"
            and self.jobs_panel is not None
        ):
            self.jobs_panel.update_job_progress(job_id, current_progress)
        if self.current_page is not None and self.current_page.objectName() == "homePage":
            self.chat_panel.update_processing_jobs(
                self.processing_jobs(), self._job_progress
            )

    def update_jobs(self, active_jobs, inactive_jobs):
        """Update both status-based job collections from the global poller."""
        self._inactive_jobs = inactive_jobs or []
        self.update_active_jobs(active_jobs)
        if (
            self.current_page is not None
            and self.current_page.objectName() == "jobsPage"
            and self.jobs_panel is not None
        ):
            self.jobs_panel.update_inactive_jobs(self._inactive_jobs)

    def processing_jobs(self):
        return [job for job in self._active_jobs if job_is_processing(job)]

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
                self.processing_jobs(), self._job_progress
            )

    def open_files(self):
        session = Agent.read_session()
        if not session:
            self.show_login()
            return

        agent_ip = session.get("agent_ip")
        user_id = session.get("user_id") or session.get("id")
        if not isinstance(agent_ip, str) or not agent_ip.strip():
            QMessageBox.warning(
                self,
                "Remote server not connected",
                "You need to connect with the remote server first. Add the agent IP address in Settings.",
            )
            return

        system = platform.system()
        folder = f"//{agent_ip.strip()}/swarif/{user_id}"

        try:
            if system == "Darwin":
                subprocess.Popen(["open", folder])
            elif system == "Windows":
                subprocess.Popen(["explorer", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except OSError as error:
            log(error)
            QMessageBox.warning(self, "Unable to open Files", str(error))

    def update_chat_messages(self, chat_messages):
        if self.current_page is None or self.current_page.objectName() != "homePage":
            return
        old_panel = self.chat_panel
        self.home_layout.removeWidget(old_panel)
        old_panel.setParent(None)
        old_panel.deleteLater()
        self.chat_panel = ChatPanel(
            chat_messages=chat_messages or [],
            newest_first=False,
            processing_jobs=self.processing_jobs(),
            job_progress=self._job_progress,
            agent_typing=self._agent_typing,
        )
        self.chat_panel.message_submitted.connect(self.message_submitted)
        self.home_layout.addWidget(self.chat_panel, 1)

    def logout(self):
        Agent.clear_session()
        self.server_connection_changed.emit()
        self.show_login()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.anchor_bottom_right)

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.isVisible() and not self._anchoring:
            QTimer.singleShot(0, self.anchor_bottom_right)

    def anchor_bottom_right(self, *args):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        margin = 18
        target_x = available.right() - self.width() - margin + 1
        target_y = available.bottom() - self.height() - margin + 1
        if self.x() == target_x and self.y() == target_y:
            return
        self._anchoring = True
        self.move(target_x, target_y)
        self._anchoring = False

    def fit_to_available_screen(self):
        """Keep the complete window card within the usable desktop area."""
        if self._compact:
            self.anchor_bottom_right()
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
        self.anchor_bottom_right()


STYLESHEET = f"""
* {{
    font-family: "Helvetica Neue";
    color: {DARK};
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
#sidebar {{ background: #F7FAFE; border-right: 1px solid {BORDER}; }}
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
#profile {{ background: #EEF5FE; border-radius: 13px; }}
#profileName {{ font-size: 13px; font-weight: 650; }}
#status, #online {{ color: #20A05A; font-size: 10px; }}
#status[connected="false"] {{ color: #D44D59; }}
#chatPanel, #conversation {{ background: white; }}
#chatHeader {{ background: white; border-bottom: 1px solid {BORDER}; }}
#assistantTitle {{ font-size: 16px; font-weight: 700; }}
#assistantSubtitle {{ color: {MUTED}; font-size: 12px; }}
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
#loginPage {{ background: #F7FAFE; }}
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
#jobsPanel, #jobsPage, #jobListContent, #settingsPanel, #settingsPage {{ background: white; }}
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
QScrollBar:vertical {{ background: transparent; width: 5px; margin: 3px; }}
QScrollBar::handle:vertical {{ background: #CAD8E8; border-radius: 2px; min-height: 28px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Swarif")
    app.setFont(QFont("Arial", 10))
    window = SwarifWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
