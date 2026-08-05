"""HB CREW 런처 — exe 옆의 crew_main.py 를 실행하기만 한다.

로직을 exe 안에 굽지 않기 때문에, 코드를 고쳐도 .py 파일만 배포하면 된다
(PD 의 pd_launcher 와 같은 방식).
"""
import sys
import os
import runpy
import traceback

from PySide6 import QtWidgets

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def show_error_popup(title, message):
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)

    msg_box = QtWidgets.QMessageBox()
    msg_box.setIcon(QtWidgets.QMessageBox.Critical)
    msg_box.setWindowTitle(title)
    msg_box.setText("HB CREW 실행 중 문제가 발생했습니다.")
    msg_box.setDetailedText(message)
    msg_box.exec()


REAL_MAIN_SCRIPT = os.path.join(BASE_DIR, "crew_main.py")

if not os.path.exists(REAL_MAIN_SCRIPT):
    show_error_popup("파일 없음", f"메인 코어 파일을 찾을 수 없습니다.\n경로: {REAL_MAIN_SCRIPT}")
    sys.exit(1)

try:
    runpy.run_path(REAL_MAIN_SCRIPT, run_name="__main__")
except Exception:
    show_error_popup("치명적 오류", traceback.format_exc())
    sys.exit(1)
