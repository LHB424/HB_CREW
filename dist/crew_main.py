"""HB CREW — 팀원용 '내 작업' 창구(읽기 전용) 진입점.

crew_launcher.exe 가 이 파일을 runpy 로 실행한다(exe 를 다시 굽지 않고 배포하기 위함).
여기서는 크래시 로깅과 앱 부트스트랩만 하고, 화면은 crew_ui.CrewWindow 가 맡는다.
"""
import sys
import os
import datetime
import traceback
import faulthandler

from PySide6 import QtWidgets, QtCore, QtGui

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 형제 모듈(crew_ui, scanner_core, common_utils)을 어떤 실행 경로에서도 찾게 한다.
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


# ── 크래시/예외 로깅 (exe·소스 모두 동작) ──────────────────────────
def _resolve_log_path():
    """로그를 쓸 수 있는 경로를 찾는다. exe 폴더 → 사용자 홈 순으로 시도.
    (공유 폴더에서 실행하면 exe 옆이 쓰기 불가일 수 있다.)"""
    candidates = [
        os.path.join(BASE_DIR, "crew_error_log.txt"),
        os.path.join(os.path.expanduser("~"), "crew_error_log.txt"),
    ]
    for p in candidates:
        try:
            with open(p, "a", encoding="utf-8"):
                pass
            return p
        except Exception:
            continue
    return candidates[-1]


LOG_PATH = _resolve_log_path()


def _write_log(header, text):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"\n===== {header} @ {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            f.write(text)
            f.write("\n")
    except Exception:
        pass


def _excepthook(exc_type, exc_value, exc_tb):
    _write_log("PYTHON EXCEPTION", "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _excepthook


def _qt_message_handler(mode, context, message):
    # Qt 슬롯(버튼 콜백) 안에서 난 예외는 excepthook 을 못 거치는 경우가 있다.
    try:
        _write_log("QT MESSAGE", f"[{mode}] {message}\n  at {context.file}:{context.line} {context.function}")
    except Exception:
        pass


try:
    QtCore.qInstallMessageHandler(_qt_message_handler)
except Exception:
    pass

try:
    _fault_fp = open(os.path.join(os.path.dirname(LOG_PATH), "crew_fault.txt"), "w", encoding="utf-8")
    faulthandler.enable(file=_fault_fp, all_threads=True)
except Exception:
    pass
# ───────────────────────────────────────────────────────────────

from crew_ui import CrewWindow, load_settings, is_valid_project  # noqa: E402  (sys.path 설정 후 import)


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")

    # OS 가 창을 처음 그릴 때 흰 배경이 번쩍이지 않도록 기본 팔레트를 다크로 덮는다.
    palette = QtGui.QPalette()
    palette.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor("#1a1a1e"))
    palette.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor("#1a1a1e"))
    app.setPalette(palette)

    last_project = load_settings().get("last_project")
    if not is_valid_project(last_project):
        last_project = None   # 폴더가 사라졌거나 프로젝트가 아니면 무시하고 선택부터

    window = CrewWindow(last_project)
    window.show()
    sys.exit(app.exec())
