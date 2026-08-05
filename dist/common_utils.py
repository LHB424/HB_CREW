import os
import re
import json
import shutil
import logging
import tempfile
from PySide6 import QtWidgets, QtCore, QtGui

# --- 1. 로깅 기본 설정 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HBPD")

# --- 1-0. 사용자별 설정 파일 경로 ---
# 설정을 exe 옆에 두면 팀 공유 폴더에서 실행할 때 팀원끼리 서로 덮어쓴다.
# HB 계열 앱(PD·CREW·Texture Publisher)은 모두 이 규칙으로 사용자 프로필 아래에 쓴다.
CONFIG_VENDOR = "HB_pipeline"

def user_config_path(app_key, filename, legacy_path=None):
    """사용자별 설정 파일의 전체 경로를 돌려준다(상위 폴더를 만들어 둔다).

    Windows : %APPDATA%/HB_pipeline/{app_key}/{filename}
    그 외    : ~/.config/HB_pipeline/{app_key}/{filename}

    legacy_path: 예전에 쓰던 경로(보통 exe 옆). 새 위치에 파일이 없고 옛 위치에
    있으면 **한 번 복사**해 기존 설정을 잇는다. 옛 파일은 지우지 않는다 —
    구버전 exe가 아직 도는 PC가 있어도 그쪽이 깨지지 않게 하기 위함.
    새 위치를 쓸 수 없는 환경이면 옛 경로로 돌아가 동작만은 유지한다.
    """
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    target_dir = os.path.join(base, CONFIG_VENDOR, app_key)
    path = os.path.join(target_dir, filename)
    try:
        os.makedirs(target_dir, exist_ok=True)
        if legacy_path and not os.path.exists(path) and os.path.exists(legacy_path):
            shutil.copy2(legacy_path, path)
            logger.info(f"설정을 사용자 폴더로 이관: {legacy_path} → {path}")
    except Exception as e:
        logger.error(f"사용자 설정 경로 준비 실패 ({path}): {e}")
        if legacy_path:
            return legacy_path
    return path

# --- 1-1. 컷/시퀀스 스캔 제외 폴더 ---
# 리뷰 탭이 만드는 _review_cache 등 컷이 아닌 시스템·캐시 폴더가
# 샷/컷 목록에 잡히는 것을 막는다. 새 시스템 폴더가 생기면 여기에만 추가하면 된다.
RESERVED_DIRS = {"_review_cache", "cache", "_metadata", "_config", "_pipeline"}

def is_shot_dir(name):
    """폴더 이름이 실제 컷/시퀀스로 취급할 대상인지 여부(예약 폴더 제외)."""
    return name.lower() not in RESERVED_DIRS

# --- 1-2. 작업 파일 판정 (셋업/템플릿 씬 제외) ---
# 진행 상태 판정에서 제외할 비(非)작업 씬 이름. 토큰 단위로만 비교한다.
IGNORE_SCENE_TOKENS = {"setup", "base", "init"}
_TOKEN_SPLIT = re.compile(r"[_\-.\s]+")

def is_work_scene(filename):
    """씬 파일 이름이 실제 작업 파일인지 여부.

    'setup' / 'base' / 'init' 은 **구분자로 나뉜 토큰과 정확히 일치할 때만** 제외한다.
    부분문자열로 비교하면 'Baseball', 'BaseCamp', 'Database' 같은 이름의 에셋이
    통째로 무시되어, pub이 있어도 영원히 Not Started 로 보인다.
    (상태 판정의 부분문자열 비교는 같은 계열의 과거 버그와 원인이 같다.)
    """
    stem = os.path.splitext(filename)[0].lower()
    return not any(t in IGNORE_SCENE_TOKENS for t in _TOKEN_SPLIT.split(stem))

# --- 1-3. 진행 상태 화면 표기 ---
# 상태 토큰(Done/In Progress/Not Started/세부 상태)은 색상·집계·진행률 계산이
# `==`로 비교하는 판정값이자 SAVER가 JSON에 쓰는 계약값이다. **토큰은 바꾸지 않고**
# 화면에 나가는 문자열만 여기서 만든다. 비교·집계에 이 함수의 결과를 쓰면 안 된다.
STATUS_NONE_LABEL = "—"

def status_label(status, version=""):
    """상태 토큰과 버전을 화면 표기로 합친다.

        Done        → PUB(v003)
        In Progress → WIP(v012)
        Not Started → —
        그 외(Layout·Blocking·Spline·Polishing 등 세부 상태) → Spline(v012)

    버전이 없으면 괄호 없이 이름만 반환한다(예: 버전 토큰이 없는 옛 파일).
    """
    s = (status or "").strip()
    ver = (version or "").strip()
    if not s or s == "Not Started":
        return STATUS_NONE_LABEL
    if s == "Done":
        name = "PUB"
    elif s == "In Progress":
        name = "WIP"
    else:
        name = s
    return f"{name}({ver})" if ver else name

# --- 2. 안전한 JSON 저장 (원자적 쓰기) ---
def safe_json_save(filepath, data):
    """파일 쓰기 중 프로그램이 종료되어도 JSON이 깨지지 않도록 안전하게 저장합니다."""
    dir_name = os.path.dirname(filepath)
    os.makedirs(dir_name, exist_ok=True)
    try:
        fd, temp_path = tempfile.mkstemp(dir=dir_name, suffix='.json')
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(temp_path, filepath)
    except Exception as e:
        logger.error(f"JSON 저장 실패 ({filepath}): {e}")

# --- 3. 공통 마감일 위젯 (코드 재사용) ---
class DeadlineWidget(QtWidgets.QWidget):
    """마감일 표시/수정 버튼 + (옵션) 완료 체크 버튼을 함께 담는 위젯.

    current_date_str : "yyyy-MM-dd" 또는 빈 문자열
    save_callback    : 날짜 문자열을 받아 저장하는 콜백
    is_done          : 완료 여부(True면 초록 '✓ 완료 D+n' 형태로 표시)
    done_callback    : 완료 토글 시 새 상태(bool)를 받아 저장하는 콜백.
                       None이면 완료 체크 버튼을 숨긴다(구버전 호환).
    """
    def __init__(self, current_date_str, save_callback, is_done=False, done_callback=None):
        super().__init__()
        self.save_callback = save_callback
        self.done_callback = done_callback
        self.is_done = bool(is_done)
        self.current_date = None

        if current_date_str:
            date = QtCore.QDate.fromString(current_date_str, "yyyy-MM-dd")
            if date.isValid():
                self.current_date = date

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # 날짜 버튼(기존 역할)
        self.date_btn = QtWidgets.QPushButton()
        self.date_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.date_btn.setMinimumWidth(80)
        self.date_btn.clicked.connect(self.open_calendar)
        layout.addWidget(self.date_btn, 1)

        # 완료 체크 버튼(done_callback이 있을 때만)
        self.done_btn = None
        if self.done_callback is not None:
            self.done_btn = QtWidgets.QPushButton()
            self.done_btn.setFixedWidth(26)
            self.done_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
            self.done_btn.clicked.connect(self.toggle_done)
            layout.addWidget(self.done_btn, 0)

        self.update_display()

    def update_display(self):
        # 날짜 부분 텍스트/색 계산
        if not self.current_date:
            date_text, color, border = "미설정", "#8b8b94", "#3a3a42"
        else:
            days_diff = QtCore.QDate.currentDate().daysTo(self.current_date)
            if days_diff > 0:
                date_text, color, border = f"D-{days_diff}", "#e8e8ec", "#3a3a42"
                if days_diff <= 3: color, border = "#F59E0B", "#F59E0B"
            elif days_diff == 0:
                date_text, color, border = "D-Day", "#EF4444", "#EF4444"
            else:
                date_text, color, border = f"D+{-days_diff}", "#EF4444", "#EF4444"

        # 완료 상태면 초록으로 덮되, 원래 D값은 뒤에 함께 표시
        if self.is_done:
            text = f"✓ 완료 ({date_text})" if self.current_date else "✓ 완료"
            color = border = "#10B981"
        else:
            text = date_text

        self.date_btn.setText(text)
        self.date_btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; border: 1px solid {border}; border-radius: 4px; color: {color}; font-weight: bold; padding: 4px; }}
            QPushButton:hover {{ background-color: #2a2a30; }}
        """)

        if self.done_btn is not None:
            if self.is_done:
                self.done_btn.setText("✓")
                self.done_btn.setToolTip("완료 해제")
                self.done_btn.setStyleSheet(
                    "QPushButton { background-color: #10B981; border: none; color: white;"
                    " border-radius: 4px; font-weight: bold; padding: 4px; }"
                    " QPushButton:hover { background-color: #0e9e74; }")
            else:
                self.done_btn.setText("○")
                self.done_btn.setToolTip("완료로 표시")
                self.done_btn.setStyleSheet(
                    "QPushButton { background-color: transparent; border: 1px solid #3a3a42;"
                    " color: #8b8b94; border-radius: 4px; font-weight: bold; padding: 4px; }"
                    " QPushButton:hover { border: 1px solid #10B981; color: #10B981; }")

    def toggle_done(self):
        self.is_done = not self.is_done
        self.update_display()
        if self.done_callback:
            self.done_callback(self.is_done)

    def open_calendar(self):
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowFlags(QtCore.Qt.Popup)
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)

        cal = QtWidgets.QCalendarWidget()
        cal.setGridVisible(True)
        cal.setStyleSheet("""
            QCalendarWidget QWidget { alternate-background-color: #242429; background-color: #1a1a1e; color: #e8e8ec; }
            QCalendarWidget QAbstractItemView:enabled { background-color: #1a1a1e; color: #e8e8ec; selection-background-color: #6366f1; }
            QCalendarWidget QToolButton { color: #e8e8ec; background-color: transparent; }
            QCalendarWidget QToolButton:hover { background-color: #2a2a30; }
        """)
        
        cal.setSelectedDate(self.current_date if self.current_date else QtCore.QDate.currentDate())
        layout.addWidget(cal)

        def on_date_selected(date):
            self.current_date = date
            self.update_display()
            self.save_callback(date.toString("yyyy-MM-dd")) # 콜백 실행
            dialog.accept()

        cal.clicked.connect(on_date_selected)
        dialog.move(self.date_btn.mapToGlobal(QtCore.QPoint(0, self.date_btn.height())))
        dialog.exec()

# --- 4. 작업자 배정(assignment.json) 저장 헬퍼 ---
def save_assignment(meta_dir, stage, worker_name):
    """에셋 폴더의 assignment.json에 단계별 담당자를 저장한다.

    meta_dir: _metadata/assets/{Category}/{에셋명} 경로
    stage:    "MDL" | "RIG" | "ANI"
    worker_name: 배정할 이름. 빈 문자열/"-"이면 해당 단계 배정을 해제(키 제거)한다.

    버전 JSON(_MDL_/_RIG_ 등)과 분리된 파일이라 아티스트의 재저장에 덮이지 않는다.
    """
    os.makedirs(meta_dir, exist_ok=True)
    path = os.path.join(meta_dir, "assignment.json")
    data = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception as e:
            logger.error(f"assignment.json 읽기 실패 ({path}): {e}")

    name = (worker_name or "").strip()
    if not name or name.startswith("-"):
        data.pop(stage, None)   # 미지정 → 키 제거
    else:
        data[stage] = name

    safe_json_save(path, data)
    return data


def save_json_key(json_path, key, value):
    """지정한 JSON 파일에 key=value를 병합 저장한다(기존 내용 보존).
    value가 None이면 해당 키를 제거한다. 완료 플래그 저장 등에 사용."""
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    data = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
        except Exception as e:
            logger.error(f"JSON 읽기 실패 ({json_path}): {e}")
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    safe_json_save(json_path, data)
    return data


# --- 5. 작업자 배정 드롭다운 위젯 (Assets/Rigging 공용) ---
class WorkerComboWidget(QtWidgets.QComboBox):
    """팀원 명단에서 작업자를 골라 assignment.json에 즉시 저장하는 콤보박스.

    member_names : 드롭다운에 채울 팀원 이름 리스트
    current_name : 현재 배정된 이름(명단에 없으면 항목으로 추가해 유지)
    save_callback: 선택된 이름(문자열)을 받아 저장하는 콜백. "-"이면 미지정.
    """
    PLACEHOLDER = "-"

    def __init__(self, member_names, current_name, save_callback):
        super().__init__()
        self.save_callback = save_callback
        self._loading = True   # 초기 채우는 동안 콜백 억제

        # 마우스 휠로 실수로 값이 바뀌지 않도록 포커스 정책 조정
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.setStyleSheet("""
            QComboBox { background-color: #242429; color: #e8e8ec; border: 1px solid #3a3a42;
                        border-radius: 4px; padding: 2px 6px; }
            QComboBox:hover { border: 1px solid #6366f1; }
            QComboBox QAbstractItemView { background-color: #1a1a1e; color: #e8e8ec;
                        selection-background-color: #6366f1; }
        """)

        self.addItem(self.PLACEHOLDER, userData="-")
        for nm in member_names:
            self.addItem(nm, userData=nm)

        cur = (current_name or "").strip()
        if cur and not cur.startswith("-"):
            idx = self.findData(cur)
            if idx < 0:                      # 명단에 없는 이름(외주 등) → 항목 추가
                self.addItem(f"{cur} (외부)", userData=cur)
                idx = self.findData(cur)
            self.setCurrentIndex(idx)
        else:
            self.setCurrentIndex(0)

        self._loading = False
        self.currentIndexChanged.connect(self._on_changed)

    def wheelEvent(self, event):
        # 콤보 위에서 마우스 휠을 돌려도 값이 바뀌지 않도록 무시하고,
        # 대신 부모(트리 스크롤)로 이벤트를 넘긴다.
        event.ignore()

    def _on_changed(self, _idx):
        if self._loading:
            return
        value = self.currentData()
        self.save_callback(value if value is not None else "-")