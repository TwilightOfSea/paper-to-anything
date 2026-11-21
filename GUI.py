from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
import sys
import threading
from ctypes import wintypes
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QFont, QFontDatabase, QIcon, QTextCursor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.pipelines import COVER_STYLES, ImageGenerationGate, RunOptions, run_task
from app.siliconflow import FREE_IMAGE_MODEL, IMAGE_MODELS, SiliconFlowClient, SiliconFlowError


DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"
PROJECT_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_DIR))
DEFAULT_OUTPUT = PROJECT_DIR / "outputs"
FONT_PATHS = (
    RESOURCE_DIR / "assets" / "HarmonyOS_Sans_SC_Regular.ttf",
    RESOURCE_DIR / "assets" / "HarmonyOS_Sans_SC_Medium.ttf",
    RESOURCE_DIR / "assets" / "HarmonyOS_Sans_SC_Bold.ttf",
)
ICON_PATH = RESOURCE_DIR / "assets" / "app_icon.ico"
LANGUAGES = ["跟随原文", "中文", "English"]
CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "PaperToAnything"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
SILICONFLOW_REGISTER_URL = "https://cloud.siliconflow.cn/i/8yRGPEoX"

TASK_ORDER = ["html", "wechat", "poster", "xhs", "slides"]
TASK_INFO = {
    "html": {
        "nav": "网页",
        "title": "论文转网页",
        "description": "单页学术项目主页，导出 HTML 与论文配图。",
        "output": "index.html",
    },
    "wechat": {
        "nav": "公众号",
        "title": "论文转公众号",
        "description": "深度解读、样式化 HTML 与公众号封面。",
        "output": "wechat_article.html",
    },
    "poster": {
        "nav": "海报",
        "title": "论文转海报",
        "description": "提炼会议海报内容，导出 HTML 与高清 PNG。",
        "output": "poster.png",
    },
    "xhs": {
        "nav": "小红书",
        "title": "论文转小红书",
        "description": "图文文案、3:4 封面与可直接发布的配图。",
        "output": "xhs_post.md",
    },
    "slides": {
        "nav": "幻灯片",
        "title": "论文转幻灯片",
        "description": "16:9 PPTX、逐页演讲稿与可选视觉审查。",
        "output": "slides.pptx",
    },
}

APP_QSS_TEMPLATE = """
* {
    font-family: "__UI_FONT__";
    font-size: 13px;
    color: #17201D;
}
QMainWindow, QWidget#appRoot {
    background: #F2F4F1;
}
QFrame#sidebar {
    background: #17221E;
    border: none;
}
QLabel#brandEyebrow {
    color: #8BC4B2;
    font-size: 11px;
    font-weight: 700;
}
QLabel#brandTitle {
    color: #FFFFFF;
    font-size: 24px;
    font-weight: 700;
}
QLabel#brandSubtitle {
    color: #9EAAA4;
    font-size: 12px;
}
QLabel#sidebarSection {
    color: #9EAAA4;
    font-size: 11px;
    font-weight: 600;
}
QLabel#sidebarStatus {
    color: #9EAAA4;
    font-size: 12px;
    font-weight: 600;
}
QLabel#sidebarStatus[state="ok"] {
    color: #8BC4B2;
}
QLabel#sidebarFootnote {
    color: #73827B;
    font-size: 11px;
}
QPushButton#taskButton {
    min-height: 44px;
    padding: 0 16px;
    background: transparent;
    color: #EAF0ED;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 0;
    text-align: left;
    font-size: 13px;
}
QPushButton#taskButton:hover {
    background: #22332D;
}
QPushButton#taskButton:checked {
    background: #29443A;
    border-left: 3px solid #4F9D82;
    color: #FFFFFF;
    font-weight: 700;
}
QFrame[card="true"] {
    background: #FFFFFF;
    border: 1px solid #DCE3DE;
    border-radius: 8px;
}
QLabel[role="pageTitle"] {
    font-size: 27px;
    font-weight: 700;
}
QLabel[role="pageSubtitle"] {
    color: #68736D;
    font-size: 13px;
}
QLabel[role="cardTitle"] {
    font-size: 16px;
    font-weight: 700;
}
QLabel[role="muted"] {
    color: #68736D;
    font-size: 12px;
}
QLabel[role="field"] {
    font-size: 12px;
    font-weight: 600;
}
QLabel#stepBadge {
    min-width: 34px;
    max-width: 34px;
    min-height: 24px;
    max-height: 24px;
    padding: 0;
    background: #E4F1EC;
    color: #22745F;
    border-radius: 5px;
    font-size: 11px;
    font-weight: 700;
}
QLabel#apiBadge {
    min-height: 30px;
    padding: 0 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 700;
}
QLabel#apiBadge[state="ok"] {
    background: #E4F1EC;
    color: #22745F;
}
QLabel#apiBadge[state="warn"] {
    background: #FBF1E3;
    color: #8B5D1F;
}
QFrame#summaryPanel {
    background: #F7F9F6;
    border: 1px solid #E5EBE6;
    border-radius: 6px;
}
QPushButton {
    min-height: 20px;
}
QPushButton[role="primary"] {
    min-height: 46px;
    padding: 0 18px;
    background: #22745F;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    font-size: 15px;
    font-weight: 700;
}
QPushButton[role="primary"]:hover {
    background: #1B604F;
}
QPushButton[role="primary"]:disabled {
    background: #9AB7AD;
    color: #F5F8F6;
}
QPushButton[role="secondary"] {
    min-height: 34px;
    padding: 0 12px;
    background: #F7F9F6;
    color: #17201D;
    border: 1px solid #DCE3DE;
    border-radius: 6px;
}
QPushButton[role="secondary"]:hover {
    background: #E4F1EC;
    border-color: #22745F;
}
QPushButton[role="quiet"] {
    min-height: 30px;
    padding: 0 8px;
    background: transparent;
    color: #68736D;
    border: none;
    border-radius: 5px;
}
QPushButton[role="quiet"]:hover {
    background: #F7F9F6;
    color: #17201D;
}
QPushButton[role="quiet"]:disabled,
QPushButton[role="secondary"]:disabled {
    color: #A1AAA5;
    background: #F1F3F0;
}
QLineEdit, QComboBox {
    min-height: 20px;
    padding: 8px 10px;
    background: #F7F9F6;
    color: #17201D;
    border: 1px solid #DCE3DE;
    border-radius: 6px;
    selection-background-color: #22745F;
    selection-color: #FFFFFF;
}
QLineEdit:focus, QComboBox:focus {
    border-color: #22745F;
}
QLineEdit:disabled, QComboBox:disabled {
    color: #7B8580;
    background: #EFF2EF;
}
QComboBox QAbstractItemView {
    padding: 4px;
    background: #FFFFFF;
    border: 1px solid #DCE3DE;
    selection-background-color: #E4F1EC;
    selection-color: #17201D;
}
QCheckBox {
    min-height: 24px;
    spacing: 8px;
    color: #17201D;
}
QTableWidget {
    background: #FFFFFF;
    alternate-background-color: #FFFFFF;
    border: 1px solid #DCE3DE;
    border-radius: 6px;
    gridline-color: transparent;
    selection-background-color: #E4F1EC;
    selection-color: #17201D;
}
QTableWidget::item {
    padding: 5px 8px;
    border-bottom: 1px solid #EEF1EE;
}
QHeaderView::section {
    padding: 7px 8px;
    background: #F7F9F6;
    color: #68736D;
    border: none;
    border-bottom: 1px solid #DCE3DE;
    font-size: 11px;
    font-weight: 600;
}
QTextEdit#logBox {
    padding: 8px;
    background: #F7F9F6;
    color: #17201D;
    border: 1px solid #E5EBE6;
    border-radius: 6px;
    font-family: "__UI_FONT__";
    font-size: 12px;
}
QScrollBar:vertical {
    width: 10px;
    margin: 2px;
    background: transparent;
}
QScrollBar::handle:vertical {
    min-height: 24px;
    background: #CAD3CD;
    border-radius: 5px;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QFrame#dialogHeader {
    background: #17221E;
    border: none;
}
QLabel#dialogTitle {
    color: #FFFFFF;
    font-size: 20px;
    font-weight: 700;
}
QLabel#dialogSubtitle {
    color: #9EAAA4;
    font-size: 12px;
}
QLabel#dialogStatus {
    color: #68736D;
    font-size: 12px;
}
QLabel#dialogStatus[tone="error"] {
    color: #B34A43;
}
"""

_UI_FONT_FAMILY: str | None = None


def _load_ui_font() -> str:
    global _UI_FONT_FAMILY
    if _UI_FONT_FAMILY:
        return _UI_FONT_FAMILY
    for path in FONT_PATHS:
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            _UI_FONT_FAMILY = families[0]
    if _UI_FONT_FAMILY:
        return _UI_FONT_FAMILY
    _UI_FONT_FAMILY = "Segoe UI"
    return _UI_FONT_FAMILY


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_string_buffer(data, len(data))
    source = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = _DataBlob()

    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    if encrypt:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "Paper to Anything API Key",
            None,
            None,
            None,
            0,
            ctypes.byref(target),
        )
    else:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(target),
        )
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def _protect_api_key(value: str) -> str:
    if not value:
        return ""
    if sys.platform == "win32":
        protected = _dpapi(value.encode("utf-8"), encrypt=True)
        return "dpapi:" + base64.b64encode(protected).decode("ascii")
    # shortcut: non-Windows uses a user-private config file; use a system
    # credential store before shipping a cross-platform release.
    return "plain:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def _unprotect_api_key(value: str) -> str:
    if not value:
        return ""
    if value.startswith("dpapi:") and sys.platform == "win32":
        protected = base64.b64decode(value.removeprefix("dpapi:"))
        return _dpapi(protected, encrypt=False).decode("utf-8")
    if value.startswith("plain:"):
        return base64.b64decode(value.removeprefix("plain:")).decode("utf-8")
    return ""


def _read_settings(path: Path = SETTINGS_FILE) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    result = {
        "model": str(data.get("model") or ""),
        "output_dir": str(data.get("output_dir") or ""),
    }
    try:
        result["api_key"] = _unprotect_api_key(str(data.get("api_key") or ""))
    except (OSError, ValueError):
        result["api_key"] = ""
    return result


def _write_settings(path: Path, api_key: str, model: str, output_dir: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "api_key": _protect_api_key(api_key.strip()),
        "model": model.strip(),
        "output_dir": output_dir.strip(),
    }
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    if sys.platform != "win32":
        path.chmod(0o600)


def _read_api_key(api_key: str | None, key_file: str | None) -> str:
    if api_key:
        return api_key.strip()
    if key_file:
        path = Path(key_file).expanduser()
        if not path.is_file():
            raise ValueError(f"API Key 文件不存在: {path}")
        return path.read_text(encoding="utf-8-sig").strip()
    env_key = os.environ.get("SILICONFLOW_API_KEY", "").strip()
    if env_key:
        return env_key
    raise ValueError("请提供 --api-key、--api-key-file 或 SILICONFLOW_API_KEY")


def _run_cli(args: argparse.Namespace) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        api_key = _read_api_key(args.api_key, args.api_key_file)
        client = SiliconFlowClient(api_key, args.model)
        if args.list_models:
            models = client.list_models()
            print(f"可用模型数量: {len(models)}")
            for model in models[:50]:
                print(model)
            if len(models) > 50:
                print(f"... 其余 {len(models) - 50} 个未显示")
            return 0
        if not args.input:
            raise ValueError("CLI 模式必须提供 --input")
        if not args.task:
            raise ValueError("CLI 模式必须提供 --task")
        options = RunOptions(
            include_images=not args.no_images,
            html_style=args.html_style,
            html_language=args.html_language,
            wechat_angle=args.wechat_angle,
            wechat_length=args.wechat_length,
            ai_cover=args.ai_cover,
            image_model=args.image_model,
            cover_style=args.cover_style,
            output_language=args.output_language,
            poster_style=args.poster_style,
            xhs_tone=args.xhs_tone,
            slides_length=args.slides_length,
            visual_qa=not args.no_visual_qa,
        )
        results = run_task(
            args.task,
            [Path(item) for item in args.input],
            client,
            Path(args.output_dir),
            options,
            ImageGenerationGate(),
            print,
        )
        failures = [result for result in results if not result.ok]
        print(f"完成 {len(results) - len(failures)}/{len(results)} 个文件")
        for result in failures:
            print(f"失败: {result.source} | {result.message}")
        return 1 if failures else 0
    except (ValueError, SiliconFlowError, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


class ApiSettingsDialog(QDialog):
    test_finished = pyqtSignal(bool, str)

    def __init__(
        self,
        api_key: str,
        model: str,
        output_dir: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.output_dir = output_dir
        self.api_key = api_key
        self.model = model
        self.setWindowTitle("API 设置")
        self.setFixedWidth(560)
        self.setWindowModality(Qt.ApplicationModal)
        self.test_finished.connect(self._finish_test)
        self._build()

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(objectName="dialogHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(24, 20, 24, 20)
        header_layout.setSpacing(4)
        title = QLabel("硅基流动连接")
        title.setObjectName("dialogTitle")
        subtitle = QLabel("API Key 会在本机加密保存，下次启动自动载入。")
        subtitle.setObjectName("dialogSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        root.addWidget(header)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(24, 20, 24, 20)
        body_layout.setSpacing(8)

        body_layout.addWidget(self._field_label("API Key"))
        key_row = QHBoxLayout()
        key_row.setSpacing(10)
        self.key_edit = QLineEdit(self.api_key)
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("sk-...")
        self.key_edit.setCursorPosition(0)
        key_row.addWidget(self.key_edit, 1)
        show_key = QCheckBox("显示")
        show_key.toggled.connect(
            lambda checked: self.key_edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        key_row.addWidget(show_key)
        body_layout.addLayout(key_row)

        body_layout.addSpacing(8)
        body_layout.addWidget(self._field_label("文本模型"))
        self.model_edit = QLineEdit(self.model)
        self.model_edit.setCursorPosition(0)
        body_layout.addWidget(self.model_edit)

        self.status_label = QLabel("")
        self.status_label.setObjectName("dialogStatus")
        self.status_label.setWordWrap(True)
        body_layout.addWidget(self.status_label, 1)

        buttons = QHBoxLayout()
        register_button = self._button("注册硅基流动 API", "secondary")
        register_button.clicked.connect(self._open_register_url)
        buttons.addWidget(register_button)
        load_button = self._button("从文件读取", "quiet")
        load_button.clicked.connect(self._load_key_file)
        buttons.addWidget(load_button)
        buttons.addStretch(1)
        self.test_button = self._button("测试连接", "secondary")
        self.test_button.clicked.connect(self._test_connection)
        buttons.addWidget(self.test_button)
        save_button = self._button("保存设置", "primary")
        save_button.setMinimumHeight(40)
        save_button.clicked.connect(self._save)
        buttons.addWidget(save_button)
        body_layout.addLayout(buttons)
        root.addWidget(body)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "field")
        return label

    def _button(self, text: str, role: str) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", role)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _set_status(self, message: str, error: bool = False) -> None:
        self.status_label.setText(message)
        self.status_label.setProperty("tone", "error" if error else "")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _load_key_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择包含 API Key 的文本文件",
            "",
            "文本文件 (*.txt);;全部文件 (*.*)",
        )
        if not path:
            return
        try:
            self.key_edit.setText(Path(path).read_text(encoding="utf-8-sig").strip())
            self._set_status("已从文件读取 API Key")
        except OSError as exc:
            self._set_status(f"读取失败：{exc}", error=True)

    def _open_register_url(self) -> None:
        if not QDesktopServices.openUrl(QUrl(SILICONFLOW_REGISTER_URL)):
            self._set_status("无法打开注册链接，请检查默认浏览器设置", error=True)

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        model = self.model_edit.text().strip()
        if not key:
            self._set_status("请输入 API Key", error=True)
            return
        if not model:
            self._set_status("请输入文本模型名称", error=True)
            return
        try:
            _write_settings(SETTINGS_FILE, key, model, self.output_dir)
        except (OSError, ValueError) as exc:
            self._set_status(f"保存失败：{exc}", error=True)
            return
        self.api_key = key
        self.model = model
        self.accept()

    def _test_connection(self) -> None:
        key = self.key_edit.text().strip()
        model = self.model_edit.text().strip()
        if not key or not model:
            self._set_status("请先填写 API Key 和文本模型", error=True)
            return
        self.test_button.setEnabled(False)
        self._set_status("正在测试连接…")

        def worker() -> None:
            try:
                client = SiliconFlowClient(key, model)
                models = client.list_models()
                sample = next((item for item in models if item == model), models[0] if models else "无")
                self.test_finished.emit(
                    True,
                    f"连接成功，可见 {len(models)} 个模型；示例：{sample}",
                )
            except Exception as exc:
                self.test_finished.emit(False, f"连接失败：{exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _finish_test(self, ok: bool, message: str) -> None:
        self.test_button.setEnabled(True)
        self._set_status(message, error=not ok)


class App(QMainWindow):
    log_message = pyqtSignal(str)
    job_finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        saved = _read_settings()
        self.api_key = saved.get("api_key", "")
        self.model = saved.get("model") or DEFAULT_MODEL
        self.output_dir = saved.get("output_dir") or str(DEFAULT_OUTPUT)

        self.files: list[str] = []
        self.busy = False
        self.current_task = "html"
        self.task_buttons: dict[str, QPushButton] = {}
        self.task_pages: dict[str, QWidget] = {}

        self.log_message.connect(self._append_log)
        self.job_finished.connect(self._finish_job)
        self.setWindowTitle("Paper to Anything")
        self.resize(1180, 820)
        self.setMinimumSize(1020, 720)
        self.font_family = _load_ui_font()
        application = QApplication.instance()
        if application is not None:
            font = QFont(self.font_family, 10)
            font.setStyleStrategy(QFont.PreferAntialias | QFont.PreferQuality)
            font.setHintingPreference(QFont.PreferFullHinting)
            application.setFont(font)
        self.setStyleSheet(
            APP_QSS_TEMPLATE.replace("__UI_FONT__", self.font_family)
        )
        self._build()
        self._select_task("html")
        self._update_connection_state()
        self.log("工作台已就绪。")

    def _build(self) -> None:
        root = QWidget(objectName="appRoot")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self._build_sidebar())
        layout.addWidget(self._build_main(), 1)

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame(objectName="sidebar")
        sidebar.setFixedWidth(230)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(22, 24, 22, 22)
        brand_layout.setSpacing(1)
        eyebrow = QLabel("PAPER", objectName="brandEyebrow")
        title = QLabel("TO ANYTHING", objectName="brandTitle")
        subtitle = QLabel("论文内容工作台", objectName="brandSubtitle")
        brand_layout.addWidget(eyebrow)
        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)
        layout.addWidget(brand)

        section = QLabel("生成任务", objectName="sidebarSection")
        section.setContentsMargins(22, 0, 22, 8)
        layout.addWidget(section)

        self.task_group = QButtonGroup(self)
        self.task_group.setExclusive(True)
        for index, task in enumerate(TASK_ORDER, 1):
            button = QPushButton(f"{index:02d}   {TASK_INFO[task]['nav']}", objectName="taskButton")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda checked=False, value=task: self._select_task(value))
            self.task_group.addButton(button)
            self.task_buttons[task] = button
            layout.addWidget(button)

        layout.addStretch(1)
        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(22, 0, 22, 20)
        footer_layout.setSpacing(5)
        self.sidebar_status = QLabel("API 未配置", objectName="sidebarStatus")
        footer_layout.addWidget(self.sidebar_status)
        footer_layout.addWidget(QLabel("所有结果保存在本机", objectName="sidebarFootnote"))
        layout.addWidget(footer)
        return sidebar

    def _build_main(self) -> QWidget:
        main = QWidget()
        layout = QVBoxLayout(main)
        layout.setContentsMargins(28, 22, 28, 22)
        layout.setSpacing(16)
        layout.addWidget(self._build_header())

        content = QHBoxLayout()
        content.setSpacing(14)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        left_layout.addWidget(self._build_files_card())
        left_layout.addWidget(self._build_options_card())
        left_layout.addStretch(1)
        content.addWidget(left, 3)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(12)
        right_layout.addWidget(self._build_run_card())
        right_layout.addWidget(self._build_log_card(), 1)
        content.addWidget(right, 2)
        layout.addLayout(content, 1)
        return main

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.page_title = QLabel()
        self.page_title.setProperty("role", "pageTitle")
        self.page_subtitle = QLabel()
        self.page_subtitle.setProperty("role", "pageSubtitle")
        titles.addWidget(self.page_title)
        titles.addWidget(self.page_subtitle)
        layout.addLayout(titles, 1)

        self.api_badge = QLabel("API 未配置", objectName="apiBadge")
        layout.addWidget(self.api_badge)
        settings_button = self._button(
            "API 设置",
            "secondary",
            self.style().standardIcon(QStyle.SP_FileDialogDetailedView),
        )
        settings_button.clicked.connect(self._open_api_settings)
        layout.addWidget(settings_button)
        return header

    def _card(self) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)
        return card, layout

    def _step_header(self, step: str, title: str, subtitle: str) -> QWidget:
        header = QWidget()
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        badge = QLabel(step, objectName="stepBadge")
        badge.setAlignment(Qt.AlignCenter)
        layout.addWidget(badge, 0, Qt.AlignTop)

        texts = QVBoxLayout()
        texts.setSpacing(2)
        title_label = QLabel(title)
        title_label.setProperty("role", "cardTitle")
        subtitle_label = QLabel(subtitle)
        subtitle_label.setProperty("role", "muted")
        subtitle_label.setWordWrap(True)
        texts.addWidget(title_label)
        texts.addWidget(subtitle_label)
        layout.addLayout(texts, 1)
        return header

    def _button(
        self,
        text: str,
        role: str,
        icon=None,
    ) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("role", role)
        button.setCursor(Qt.PointingHandCursor)
        if icon is not None:
            button.setIcon(icon)
            button.setIconSize(QSize(16, 16))
        return button

    def _build_files_card(self) -> QFrame:
        card, layout = self._card()
        layout.addWidget(self._step_header("01", "输入论文", "PDF / TXT / MD / HTML"))

        self.file_table = QTableWidget(0, 2)
        self.file_table.setHorizontalHeaderLabels(["文件名", "所在位置"])
        self.file_table.verticalHeader().hide()
        self.file_table.setShowGrid(False)
        self.file_table.setWordWrap(False)
        self.file_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.file_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.file_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.file_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.file_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.file_table.setMinimumHeight(126)
        self.file_table.setMaximumHeight(148)
        layout.addWidget(self.file_table)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        add_button = self._button(
            "添加文件",
            "secondary",
            self.style().standardIcon(QStyle.SP_FileIcon),
        )
        add_button.clicked.connect(self._add_files)
        footer.addWidget(add_button)
        footer.addStretch(1)
        self.file_count_label = QLabel("尚未添加文件")
        self.file_count_label.setProperty("role", "muted")
        footer.addWidget(self.file_count_label)
        remove_button = self._button("移除", "quiet")
        remove_button.clicked.connect(self._remove_files)
        footer.addWidget(remove_button)
        clear_button = self._button(
            "清空",
            "quiet",
            self.style().standardIcon(QStyle.SP_DialogResetButton),
        )
        clear_button.clicked.connect(self._clear_files)
        footer.addWidget(clear_button)
        layout.addLayout(footer)
        return card

    def _build_options_card(self) -> QFrame:
        card, layout = self._card()
        layout.addWidget(self._step_header("02", "生成设置", "按当前任务调整内容与版式"))
        self.option_stack = QStackedWidget()
        self.option_stack.setFixedHeight(166)
        self._build_html_options()
        self._build_wechat_options()
        self._build_poster_options()
        self._build_xhs_options()
        self._build_slides_options()
        layout.addWidget(self.option_stack)
        return card

    def _option_page(self) -> tuple[QWidget, QGridLayout]:
        page = QWidget()
        grid = QGridLayout(page)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        return page, grid

    def _field(self, label: str, widget: QWidget) -> QWidget:
        field = QWidget()
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        text = QLabel(label)
        text.setProperty("role", "field")
        layout.addWidget(text)
        layout.addWidget(widget)
        return field

    def _combo(self, values: list[str], current: int = 0) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        combo.setCurrentIndex(current)
        combo.setMinimumHeight(38)
        return combo

    def _build_html_options(self) -> None:
        page, grid = self._option_page()
        self.html_style = self._combo(
            ["自动匹配", "编辑杂志风", "产品落地页", "极简学术风", "数据仪表盘", "终端技术风"]
        )
        self.html_language = self._combo(LANGUAGES)
        grid.addWidget(self._field("设计语言", self.html_style), 0, 0)
        grid.addWidget(self._field("页面语言", self.html_language), 0, 1)
        self.html_images = QCheckBox("使用论文配图")
        self.html_images.setChecked(True)
        grid.addWidget(self.html_images, 1, 0, 1, 2)
        self.task_pages["html"] = page
        self.option_stack.addWidget(page)

    def _build_wechat_options(self) -> None:
        page, grid = self._option_page()
        self.wechat_angle = self._combo(
            ["通俗科研解读", "偏方法细节", "偏应用价值", "偏结果与启示"]
        )
        self.wechat_length = self._combo(["1500", "1800", "2200", "2600"], 1)
        grid.addWidget(self._field("写作角度", self.wechat_angle), 0, 0)
        grid.addWidget(self._field("目标字数", self.wechat_length), 0, 1)
        self.wechat_images = QCheckBox("使用论文配图")
        self.wechat_images.setChecked(True)
        grid.addWidget(self.wechat_images, 1, 0, 1, 2)
        # AI cover controls are hidden in the GUI for now; CLI options remain available.
        self.task_pages["wechat"] = page
        self.option_stack.addWidget(page)

    def _build_poster_options(self) -> None:
        page, grid = self._option_page()
        self.poster_style = self._combo(["自动匹配", "学术简约", "数据叙事"])
        self.poster_language = self._combo(LANGUAGES, 1)
        grid.addWidget(self._field("视觉风格", self.poster_style), 0, 0)
        grid.addWidget(self._field("内容语言", self.poster_language), 0, 1)
        self.poster_images = QCheckBox("使用论文配图")
        self.poster_images.setChecked(True)
        grid.addWidget(self.poster_images, 1, 0, 1, 2)
        self.task_pages["poster"] = page
        self.option_stack.addWidget(page)

    def _build_xhs_options(self) -> None:
        page, grid = self._option_page()
        self.xhs_tone = self._combo(["专业科普", "研究亮点", "轻松解读"])
        self.xhs_language = self._combo(LANGUAGES, 1)
        grid.addWidget(self._field("文案语气", self.xhs_tone), 0, 0)
        grid.addWidget(self._field("内容语言", self.xhs_language), 0, 1)
        self.xhs_images = QCheckBox("使用论文配图")
        self.xhs_images.setChecked(True)
        grid.addWidget(self.xhs_images, 1, 0, 1, 2)
        # AI cover controls are hidden in the GUI for now; CLI options remain available.
        self.task_pages["xhs"] = page
        self.option_stack.addWidget(page)

    def _build_slides_options(self) -> None:
        page, grid = self._option_page()
        self.slides_length = self._combo(["8", "10", "12"], 1)
        self.slides_language = self._combo(LANGUAGES, 1)
        grid.addWidget(self._field("内容页数", self.slides_length), 0, 0)
        grid.addWidget(self._field("内容语言", self.slides_language), 0, 1)
        checks = QWidget()
        checks_layout = QHBoxLayout(checks)
        checks_layout.setContentsMargins(0, 0, 0, 0)
        checks_layout.setSpacing(18)
        self.slides_images = QCheckBox("使用论文配图")
        self.slides_images.setChecked(True)
        self.slides_visual_qa = QCheckBox("AI 视觉审查")
        self.slides_visual_qa.setChecked(True)
        checks_layout.addWidget(self.slides_images)
        checks_layout.addWidget(self.slides_visual_qa)
        checks_layout.addStretch(1)
        grid.addWidget(checks, 1, 0, 1, 3)
        self.task_pages["slides"] = page
        self.option_stack.addWidget(page)

    def _ai_cover_row(self) -> tuple[QWidget, QCheckBox, QComboBox]:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        cover = QCheckBox("AI 生成封面")
        layout.addWidget(cover)
        label = QLabel("生图模型")
        label.setProperty("role", "field")
        layout.addWidget(label)
        model = self._combo(IMAGE_MODELS)
        model.setEnabled(False)
        cover.toggled.connect(lambda checked: self._toggle_image_model(checked, model))
        layout.addWidget(model, 1)
        return row, cover, model

    def _build_run_card(self) -> QFrame:
        card, layout = self._card()
        layout.addWidget(self._step_header("03", "输出与运行", "确认后开始生成"))

        summary = QFrame(objectName="summaryPanel")
        summary_layout = QGridLayout(summary)
        summary_layout.setContentsMargins(12, 10, 12, 10)
        summary_layout.setHorizontalSpacing(12)
        summary_layout.setVerticalSpacing(5)
        summary_layout.addWidget(self._muted_label("任务"), 0, 0)
        summary_layout.addWidget(self._muted_label("文件"), 1, 0)
        self.summary_task = QLabel()
        self.summary_task.setStyleSheet("font-weight: 700;")
        self.summary_files = QLabel()
        self.summary_files.setStyleSheet("font-weight: 700;")
        summary_layout.addWidget(self.summary_task, 0, 1)
        summary_layout.addWidget(self.summary_files, 1, 1)
        summary_layout.setColumnStretch(1, 1)
        layout.addWidget(summary)

        output_label = QLabel("输出目录")
        output_label.setProperty("role", "field")
        layout.addWidget(output_label)
        output_row = QHBoxLayout()
        output_row.setSpacing(8)
        self.output_edit = QLineEdit(self.output_dir)
        self.output_edit.setCursorPosition(0)
        output_row.addWidget(self.output_edit, 1)
        choose_button = self._button(
            "选择",
            "secondary",
            self.style().standardIcon(QStyle.SP_DirIcon),
        )
        choose_button.clicked.connect(self._choose_output)
        output_row.addWidget(choose_button)
        layout.addLayout(output_row)

        self.run_button = self._button(
            "开始生成网页",
            "primary",
            self.style().standardIcon(QStyle.SP_MediaPlay),
        )
        self.run_button.clicked.connect(lambda: self._start(self.current_task))
        layout.addWidget(self.run_button)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.run_status = QLabel("等待开始")
        self.run_status.setProperty("role", "muted")
        footer.addWidget(self.run_status)
        footer.addStretch(1)
        open_button = self._button(
            "打开输出目录",
            "quiet",
            self.style().standardIcon(QStyle.SP_DirOpenIcon),
        )
        open_button.clicked.connect(self._open_output)
        footer.addWidget(open_button)
        layout.addLayout(footer)
        return card

    def _build_log_card(self) -> QFrame:
        card, layout = self._card()
        header = QHBoxLayout()
        title = QLabel("运行记录")
        title.setProperty("role", "cardTitle")
        header.addWidget(title)
        header.addStretch(1)
        clear_button = self._button(
            "清空",
            "quiet",
            self.style().standardIcon(QStyle.SP_DialogResetButton),
        )
        clear_button.clicked.connect(self._clear_log)
        header.addWidget(clear_button)
        layout.addLayout(header)
        self.log_box = QTextEdit(objectName="logBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(150)
        layout.addWidget(self.log_box, 1)
        return card

    @staticmethod
    def _muted_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "muted")
        return label

    def _select_task(self, task: str) -> None:
        self.current_task = task
        self.task_buttons[task].setChecked(True)
        self.option_stack.setCurrentWidget(self.task_pages[task])
        info = TASK_INFO[task]
        self.page_title.setText(info["title"])
        self.page_subtitle.setText(info["description"])
        if not self.busy:
            self.run_button.setText(f"开始生成{info['nav']}")
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        info = TASK_INFO[self.current_task]
        self.summary_task.setText(info["nav"])
        self.summary_files.setText(f"{len(self.files)} 个文件")
        self.file_count_label.setText(
            "尚未添加文件" if not self.files else f"已添加 {len(self.files)} 个文件"
        )

    def _toggle_image_model(self, checked: bool, combo: QComboBox) -> None:
        combo.setEnabled(checked)
        if checked and combo.currentText() != FREE_IMAGE_MODEL:
            self.log("注意：只有 Kwai-Kolors/Kolors 是免费模型。")

    def _add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择论文文件",
            "",
            "支持的文件 (*.pdf *.txt *.md *.markdown *.html *.htm);;全部文件 (*.*)",
        )
        for path in paths:
            if path not in self.files:
                self.files.append(path)
        self._refresh_file_table()

    def _refresh_file_table(self) -> None:
        self.file_table.setRowCount(len(self.files))
        for row, path in enumerate(self.files):
            source = Path(path)
            name = QTableWidgetItem(source.name)
            name.setToolTip(path)
            location = QTableWidgetItem(str(source.parent))
            location.setToolTip(str(source.parent))
            self.file_table.setItem(row, 0, name)
            self.file_table.setItem(row, 1, location)
        self._refresh_summary()

    def _remove_files(self) -> None:
        rows = sorted(
            {index.row() for index in self.file_table.selectionModel().selectedRows()},
            reverse=True,
        )
        for row in rows:
            del self.files[row]
        self._refresh_file_table()

    def _clear_files(self) -> None:
        self.files.clear()
        self._refresh_file_table()

    def _choose_output(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self,
            "选择输出目录",
            str(Path(self.output_edit.text()).expanduser()),
        )
        if path:
            self.output_edit.setText(path)

    def _open_output(self) -> None:
        path = Path(self.output_edit.text()).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                QMessageBox.information(self, "输出目录", str(path))
        except OSError as exc:
            QMessageBox.critical(self, "打开失败", str(exc))

    def _open_api_settings(self) -> None:
        dialog = ApiSettingsDialog(
            self.api_key,
            self.model,
            self.output_edit.text(),
            self,
        )
        if dialog.exec_() == QDialog.Accepted:
            self.api_key = dialog.api_key
            self.model = dialog.model
            self._update_connection_state()
            self.log("API 设置已保存，下次启动会自动载入。")

    def _update_connection_state(self) -> None:
        configured = bool(self.api_key.strip())
        self.api_badge.setText("API 已保存" if configured else "API 未配置")
        self.api_badge.setProperty("state", "ok" if configured else "warn")
        self.sidebar_status.setText("API 已配置" if configured else "API 未配置")
        self.sidebar_status.setProperty("state", "ok" if configured else "")
        for widget in (self.api_badge, self.sidebar_status):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def _save_preferences(self) -> None:
        key = self.api_key.strip()
        if not key:
            return
        try:
            _write_settings(
                SETTINGS_FILE,
                key,
                self.model.strip(),
                self.output_edit.text().strip(),
            )
        except (OSError, ValueError) as exc:
            self.log(f"设置保存失败：{exc}")

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.busy = busy
        self.run_button.setEnabled(not busy)
        self.run_button.setText(
            "正在生成…"
            if busy
            else f"开始生成{TASK_INFO[self.current_task]['nav']}"
        )
        if message:
            self.run_status.setText(message)

    def _start(self, task: str) -> None:
        if self.busy:
            QMessageBox.information(self, "正在运行", "当前已有任务正在运行")
            return
        key = self.api_key.strip()
        model = self.model.strip()
        if not key or not model:
            self._open_api_settings()
            return
        if not self.files:
            QMessageBox.warning(self, "缺少文件", "请先添加一个或多个输入文件")
            return

        if task == "html":
            options = RunOptions(
                include_images=self.html_images.isChecked(),
                html_style=self.html_style.currentText(),
                html_language=self.html_language.currentText(),
            )
        elif task == "wechat":
            options = RunOptions(
                include_images=self.wechat_images.isChecked(),
                wechat_angle=self.wechat_angle.currentText(),
                wechat_length=int(self.wechat_length.currentText()),
            )
        elif task == "poster":
            options = RunOptions(
                include_images=self.poster_images.isChecked(),
                output_language=self.poster_language.currentText(),
                poster_style=self.poster_style.currentText(),
            )
        elif task == "xhs":
            options = RunOptions(
                include_images=self.xhs_images.isChecked(),
                output_language=self.xhs_language.currentText(),
                xhs_tone=self.xhs_tone.currentText(),
            )
        else:
            options = RunOptions(
                include_images=self.slides_images.isChecked(),
                output_language=self.slides_language.currentText(),
                poster_style="自动匹配",
                slides_length=int(self.slides_length.currentText()),
                visual_qa=self.slides_visual_qa.isChecked(),
            )
        files = [Path(item) for item in self.files]
        output = Path(self.output_edit.text()).expanduser()
        self._save_preferences()
        self._set_busy(True, "正在生成，请保持窗口开启")

        def worker() -> None:
            try:
                client = SiliconFlowClient(key, model)
                results = run_task(
                    task,
                    files,
                    client,
                    output,
                    options,
                    ImageGenerationGate(),
                    self.log,
                )
                ok = sum(result.ok for result in results)
                self.log(f"批处理结束：成功 {ok}/{len(results)}")
                if ok:
                    self.log(f"输出目录：{(output / task).resolve()}")
            except Exception as exc:
                self.log(f"任务失败：{exc}")
            finally:
                self.job_finished.emit()

        threading.Thread(target=worker, daemon=True).start()

    def _finish_job(self) -> None:
        self._set_busy(False, "任务已结束，查看运行记录")

    def _clear_log(self) -> None:
        self.log_box.clear()

    def _append_log(self, message: str) -> None:
        self.log_box.append(message)
        self.log_box.moveCursor(QTextCursor.End)

    def log(self, message: str) -> None:
        self.log_message.emit(message)

    def closeEvent(self, event) -> None:
        self._save_preferences()
        super().closeEvent(event)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Paper to Anything GUI/CLI")
    parser.add_argument("--cli", action="store_true", help="启用命令行模式")
    parser.add_argument("--input", nargs="+", help="一个或多个 PDF/TXT/MD/HTML 文件")
    parser.add_argument(
        "--task",
        choices=["html", "wechat", "poster", "xhs", "slides"],
        help="要执行的流水线",
    )
    parser.add_argument("--api-key", help="硅基流动 API Key")
    parser.add_argument("--api-key-file", help="从文本文件读取 API Key")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="硅基流动文本模型名称")
    parser.add_argument("--list-models", action="store_true", help="列出账号可用模型")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT), help="输出根目录")
    parser.add_argument("--no-images", action="store_true", help="不复制论文图片")
    parser.add_argument("--html-style", default="自动匹配")
    parser.add_argument("--html-language", default="跟随原文")
    parser.add_argument("--wechat-angle", default="通俗科研解读")
    parser.add_argument("--wechat-length", type=int, default=1800)
    parser.add_argument("--output-language", choices=LANGUAGES, default="跟随原文")
    parser.add_argument("--poster-style", choices=["自动匹配", "学术简约", "数据叙事"], default="自动匹配")
    parser.add_argument("--xhs-tone", choices=["专业科普", "研究亮点", "轻松解读"], default="专业科普")
    parser.add_argument("--slides-length", type=int, choices=[8, 10, 12], default=10)
    parser.add_argument("--no-visual-qa", action="store_true", help="跳过幻灯片 AI 视觉审查")
    parser.add_argument("--ai-cover", action="store_true", help="使用硅基流动模型生成公众号或小红书封面")
    parser.add_argument("--image-model", default=FREE_IMAGE_MODEL, choices=IMAGE_MODELS)
    parser.add_argument("--cover-style", choices=COVER_STYLES, default="极简留白风")
    return parser


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args()
    if args.cli:
        return _run_cli(args)

    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    if hasattr(QApplication, "setHighDpiScaleFactorRoundingPolicy"):
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Paper to Anything")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    app.setStyle("Fusion")
    window = App()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
