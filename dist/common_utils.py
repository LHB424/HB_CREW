import os
import re
import json
import shutil
import logging
from PySide6 import QtWidgets, QtCore, QtGui

# --- 1. 로깅 기본 설정 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("HBPD")

# 로그 한 줄이 앱을 죽이지 않게 한다.
# 이 환경의 콘솔은 cp949라 `—`(U+2014) 같은 문자를 인코딩하지 못한다. 기록 문구에
# 한글이 많아 자연히 섞인다. StreamHandler가 UnicodeEncodeError를 내면 logging은
# handleError에서 스택을 다시 찍는데 그 출력도 같은 이유로 터져, 예외가 호출부까지
# 올라간다 — **탭 로드 실패를 기록하려던 로그 한 줄이 대시보드 전체를 못 뜨게 한다**
# (FailedTab 안전망이 통째로 무력화된다).
logging.raiseExceptions = False
for _handler in logging.getLogger().handlers:
    _stream = getattr(_handler, "stream", None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass

# --- 1-0. 사용자별 설정 파일 경로 ---
# 설정을 exe 옆에 두면 팀 공유 폴더에서 실행할 때 팀원끼리 서로 덮어쓴다.
# HB 계열 앱(PD·CREW·Texture Publisher)은 모두 이 규칙으로 사용자 프로필 아래에 쓴다.
CONFIG_VENDOR = "HB_pipeline"

# 저장 직전 내용을 남겨두는 사본의 꼬리표 (safe_json_save / load_json 이 함께 쓴다)
BACKUP_SUFFIX = ".bak"

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

# --- 1-2-1. 버전 메타 JSON 판정 ---
# 메타 폴더에는 성격이 다른 JSON이 섞여 있다.
#   버전 기록  `{SC}_{C}_ANI_pub_v003.json`  ← SAVER가 저장할 때 만든다(상태·작업자)
#   배정 기록  `assignment.json`             ← PD가 담당자·마감을 지정할 때 만든다
#   임시 파일  `tmpXXXX.json`                ← safe_json_save 가 쓰는 동안만 존재
# "가장 최근 파일"만 보고 상태를 읽거나 쓰면 배정 기록·임시 파일이 걸린다.
# 상태(status)의 주인은 버전 기록 하나뿐이다.
_VERSION_META_RE = re.compile(r"_v\d+\.json$", re.IGNORECASE)

def is_version_meta(filename, stage=None):
    """파일 이름이 버전 기록 JSON인지 여부. stage를 주면 그 단계('_ANI_' 등)로 한정한다."""
    if not _VERSION_META_RE.search(filename):
        return False
    return f"_{stage}_".lower() in filename.lower() if stage else True

# --- 1-2-2. 버전 메타의 자리 (단계/종류 폴더 ↔ 옛 평면 구조) ---
# 생산자 툴은 버전 기록 JSON과 썸네일을 `{메타폴더}/{STAGE}/{wip|pub}/` 아래에 쓴다.
# 그 전에는 메타 폴더 바로 밑에 전부 쌓았고, 그렇게 만들어진 프로젝트가 이미 있다.
# **읽는 쪽은 두 자리를 모두 본다** — 옛 프로젝트에서 작업자·상태가 통째로 사라지지
# 않게 하려는 것이고, 새로 저장한 것만 새 자리에 생긴다.
# `assignment.json`(배정)·`scene_config.json`(씬 마감)은 예나 지금이나 메타 폴더
# 바로 밑이다. 주인이 PD 대시보드라 버전과 무관하다.
STAGE_TOKENS = ("MDL", "RIG", "TEX", "ANI", "LGT")
META_KINDS = ("wip", "pub")

_META_PLACE_RE = re.compile(r"_(?P<stage>[A-Za-z]+)_(?:(?P<kind>wip|pub)_)?v\d+$",
                            re.IGNORECASE)

def meta_place_of(base_name):
    """버전 파일 이름에서 (STAGE, kind)를 뽑는다. 규칙 밖 이름이면 None.

        Father_MDL_pub_v003   → ("MDL", "pub")
        Father_Body_TEX_v003  → ("TEX", "pub")

    Texture Publisher 의 파일명에는 wip/pub 토큰이 없다 — TEX 는 wip 단계 자체가
    없어 퍼블리시뿐이므로, 종류가 빠진 이름은 pub 으로 본다.
    """
    m = _META_PLACE_RE.search(os.path.splitext(base_name)[0])
    if not m:
        return None
    return m.group("stage").upper(), (m.group("kind") or "pub").lower()

def meta_version_dir(meta_dir, stage, kind):
    """버전 메타(JSON·썸네일)를 **쓸** 폴더. 생산자 툴과 같은 규약."""
    return os.path.join(meta_dir, stage, kind)

def meta_file_path(meta_dir, base_name, ext):
    """씬 이름으로 메타 파일을 **읽을** 때의 경로.

    새 자리에 있으면 그 경로, 없으면 옛 평면 경로를 돌려준다. 둘 다 없을 때도
    평면 경로를 돌려주므로, 부르는 쪽은 지금처럼 존재 여부만 확인하면 된다.
    """
    place = meta_place_of(base_name)
    if place:
        path = os.path.join(meta_dir, place[0], place[1], base_name + ext)
        if os.path.exists(path):
            return path
    return os.path.join(meta_dir, base_name + ext)

def _scan_dir_entries(dir_path):
    """(파일이름들, 폴더이름들). 없거나 못 읽으면 빈 목록 — 스캔을 멈추지 않는다."""
    files, dirs = [], []
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                try:
                    (dirs if entry.is_dir() else files).append(entry.name)
                except OSError:
                    continue
    except OSError:
        pass
    return files, dirs

def iter_version_metas(meta_dir, stage=None):
    """메타 폴더의 버전 기록 JSON을 (파일이름, 전체경로)로 모아 돌려준다.

    새 구조(`{STAGE}/{wip|pub}/`)와 옛 평면 구조를 함께 훑는다. stage를 주면
    그 단계 폴더만 들어간다 — 클라우드 드라이브에서는 폴더 조회 한 번이 비싸므로
    **실제로 있는 폴더에만** 들어간다.
    """
    result = []
    files, dirs = _scan_dir_entries(meta_dir)
    for name in files:
        if is_version_meta(name, stage):
            result.append((name, os.path.join(meta_dir, name)))

    wanted = {stage.upper()} if stage else set(STAGE_TOKENS)
    for dname in dirs:
        if dname.upper() not in wanted:
            continue
        stage_dir = os.path.join(meta_dir, dname)
        _, kind_dirs = _scan_dir_entries(stage_dir)
        for kname in kind_dirs:
            if kname.lower() not in META_KINDS:
                continue
            kind_dir = os.path.join(stage_dir, kname)
            kfiles, _ = _scan_dir_entries(kind_dir)
            for name in kfiles:
                if is_version_meta(name, stage):
                    result.append((name, os.path.join(kind_dir, name)))
    return result

def latest_version_meta(meta_dir, stage=None):
    """메타 폴더에서 **가장 최근에 저장된 버전 기록 JSON**의 전체 경로. 없으면 None.

    mtime 조회는 파일마다 개별로 감싼다 — 목록을 만든 뒤 조회하기까지의 사이에
    파일이 사라질 수 있고(임시 파일·동기화 중인 클라우드 폴더), 정렬 키에서
    예외가 나면 호출한 쪽의 스캔 전체가 무효가 된다.
    """
    latest_path, latest_mtime = None, -1.0
    for _name, path in iter_version_metas(meta_dir, stage):
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            continue
        if mtime > latest_mtime:
            latest_path, latest_mtime = path, mtime
    return latest_path

# --- 1-3. 진행 상태 화면 표기 ---
# 상태 토큰(Done/In Progress/Not Started/세부 상태)은 색상·집계·진행률 계산이
# `==`로 비교하는 판정값이자 SAVER가 JSON에 쓰는 계약값이다. **토큰은 바꾸지 않고**
# 화면에 나가는 문자열만 여기서 만든다. 비교·집계에 이 함수의 결과를 쓰면 안 된다.
STATUS_NONE_LABEL = "—"

# '해당 없음' — 이 에셋/컷에는 **애초에 그 단계가 없다**(리깅하지 않는 소품 등).
# 'Not Started'(해야 하는데 아직 안 함)와 반드시 구분한다. 섞이면 끝날 수 없는
# 일이 분모에 남아 진행률이 영원히 100%에 닿지 못한다.
# 표시 주체는 PD — assignment.json 의 na_stages 목록에 스테이지 토큰을 넣는다.
STATUS_NA = "N/A"
STATUS_NA_LABEL = "해당없음"
NA_STAGES_KEY = "na_stages"

def status_label(status, version=""):
    """상태 토큰과 버전을 화면 표기로 합친다.

        Done        → PUB(v003)
        In Progress → WIP(v012)
        Not Started → —
        N/A         → 해당없음
        그 외(Layout·Blocking·Spline·Polishing 등 세부 상태) → Spline(v012)

    버전이 없으면 괄호 없이 이름만 반환한다(예: 버전 토큰이 없는 옛 파일).
    """
    s = (status or "").strip()
    ver = (version or "").strip()
    if not s or s == "Not Started":
        return STATUS_NONE_LABEL
    if s == STATUS_NA:
        return STATUS_NA_LABEL
    if s == "Done":
        name = "PUB"
    elif s == "In Progress":
        name = "WIP"
    else:
        name = s
    return f"{name}({ver})" if ver else name

# --- 2. 안전한 JSON 저장 (원자적 쓰기) ---
def safe_json_save(filepath, data):
    """JSON을 **제자리에** 덮어쓰고, 직전 내용을 `.bak`으로 남긴다.

    예전에는 임시 파일에 쓴 뒤 `os.replace`로 갈아끼웠다(원자적 교체). 로컬
    디스크에선 가장 안전한 방식이지만, **클라우드 동기화 폴더에서는 사본이
    증식한다.** 드라이브는 파일을 이름이 아니라 내부 ID로 식별하므로, 갈아끼운
    파일은 "같은 파일의 새 버전"이 아니라 **새 파일**로 업로드된다. 이름이
    같은 파일 여러 벌이 허용되니 저장할 때마다 한 벌씩 쌓인다.

    그래서 파일 자체는 제자리에 덮어써 ID를 유지하고(사본 증식 없음), 쓰는
    도중 중단돼 내용이 깨질 경우를 대비해 직전 내용을 `.bak`에 남긴다.
    읽는 쪽은 load_json 이 깨진 파일을 만나면 `.bak`으로 되돌아간다.

    `.bak`은 `_v###.json` 형식이 아니므로 버전 기록 판정(is_version_meta)과
    스캐너 집계에 걸리지 않는다.
    """
    dir_name = os.path.dirname(filepath)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    try:
        text = json.dumps(data, ensure_ascii=False, indent=4)
    except Exception as e:
        logger.error(f"JSON 변환 실패 ({filepath}): {e}")
        return
    try:
        # 직전 내용 백업. 실패해도 저장 자체는 진행한다(백업은 어디까지나 안전망).
        if os.path.exists(filepath):
            try:
                shutil.copyfile(filepath, filepath + BACKUP_SUFFIX)
            except Exception as e:
                logger.warning(f"직전 내용 백업 실패 ({filepath}): {e}")
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        logger.error(f"JSON 저장 실패 ({filepath}): {e}")


def load_json(filepath, default=None):
    """JSON을 읽어 dict로 돌려준다. 본 파일이 깨져 있으면 `.bak`으로 되돌아간다.

    safe_json_save 가 제자리 쓰기를 하므로, 쓰는 도중 중단되면 본 파일이
    잘린 채 남을 수 있다. 그때 그냥 빈 값을 돌려주면 읽고-고쳐-쓰는 쪽이
    빈 상태에서 다시 시작해 **배정이 조용히 사라진다.** 그래서 여기서 막는다.
    """
    if default is None:
        default = {}
    for path, is_backup in ((filepath, False), (filepath + BACKUP_SUFFIX, True)):
        if not os.path.exists(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            if is_backup:
                logger.warning(f"본 파일이 깨져 직전 백업을 사용한다: {filepath}")
            return data
        except Exception as e:
            logger.error(f"JSON 읽기 실패 ({path}): {e}")
    return default

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
    data = load_json(path)

    name = (worker_name or "").strip()
    if not name or name.startswith("-"):
        data.pop(stage, None)   # 미지정 → 키 제거
    else:
        data[stage] = name

    safe_json_save(path, data)
    return data


def na_stages_of(assignment):
    """assignment.json 내용에서 '해당 없음'으로 표시된 단계 집합을 뽑는다.

    assignment: load_json 으로 읽은 dict (파일이 없으면 빈 dict)
    반환: {"RIG", "TEX"} 같은 대문자 스테이지 토큰 집합. 키가 없으면 빈 집합이라
          옛 프로젝트는 지금까지와 똑같이 동작한다.

    형식이 깨져 있어도(리스트가 아님 등) 예외 없이 빈 집합을 돌려준다 — 표시용
    부가 정보 하나 때문에 스캔 전체가 무효가 되면 안 된다.
    """
    if not isinstance(assignment, dict):
        return set()
    raw = assignment.get(NA_STAGES_KEY)
    if not isinstance(raw, list):
        return set()
    return {str(s).strip().upper() for s in raw if str(s).strip()}


def save_stage_na(meta_dir, stage, is_na):
    """assignment.json 의 na_stages 목록에 단계를 넣거나 뺀다.

    meta_dir: 에셋 `_metadata/assets/{Category}/{에셋}` 또는
              컷 `_metadata/shots/{씬}/{컷}` 메타 폴더
    stage   : "RIG" | "TEX" | "LGT" 등 스테이지 토큰
    is_na   : True면 '해당 없음', False면 해제

    작업 폴더와 씬 파일은 건드리지 않는다 — 표시만 바꾸므로 해제하면 그대로
    돌아온다. 목록이 비면 키째 지워 파일에 빈 흔적을 남기지 않는다.
    """
    stage = (stage or "").strip().upper()
    path = os.path.join(meta_dir, "assignment.json")
    data = load_json(path)
    stages = na_stages_of(data)
    if is_na:
        stages.add(stage)
    else:
        stages.discard(stage)
    if stages:
        data[NA_STAGES_KEY] = sorted(stages)
    else:
        data.pop(NA_STAGES_KEY, None)
    os.makedirs(meta_dir, exist_ok=True)
    safe_json_save(path, data)
    return data


def save_json_key(json_path, key, value):
    """지정한 JSON 파일에 key=value를 병합 저장한다(기존 내용 보존).
    value가 None이면 해당 키를 제거한다. 완료 플래그 저장 등에 사용."""
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    data = load_json(json_path)
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    safe_json_save(json_path, data)
    return data


# --- 4-1. 휠로 값이 바뀌지 않는 입력 위젯 ---
# 마우스 휠은 **스크롤 수단이지 값 변경 수단이 아니다.** 표나 폼을 스크롤하다
# 콤보·스핀 위를 지나가면 값이 조용히 바뀌고, 바뀐 줄도 모른 채 저장된다.
# 되돌릴 단서도 없다(무엇이 언제 바뀌었는지 화면에 남지 않는다).
#
# **수치·선택 입력을 새로 만들 때는 QComboBox/QSpinBox 를 직접 쓰지 말고 여기
# 것을 쓴다.** 이미 만들어진 위젯이나 여기 없는 종류는 block_wheel() 로 막는다.
#
# 두 방식의 차이:
#   서브클래스 — 휠을 무시(ignore)해 **부모가 대신 스크롤**한다. 표·스크롤
#                영역 안에 있는 입력에 맞다.
#   block_wheel — 휠을 삼킨다(부모도 스크롤하지 않는다). 스크롤할 것이 없는
#                다이얼로그 안의 입력에 맞다.

class _NoWheelMixin:
    """휠 이벤트를 받지 않는다. 부모(표·스크롤 영역)가 대신 처리한다."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # 휠로 포커스가 옮겨가며 값이 바뀌는 경로도 함께 막는다.
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event):
        event.ignore()


class NoWheelComboBox(_NoWheelMixin, QtWidgets.QComboBox):
    pass


class NoWheelSpinBox(_NoWheelMixin, QtWidgets.QSpinBox):
    pass


class NoWheelDoubleSpinBox(_NoWheelMixin, QtWidgets.QDoubleSpinBox):
    pass


class NoWheelDateEdit(_NoWheelMixin, QtWidgets.QDateEdit):
    pass


class NoWheelSlider(_NoWheelMixin, QtWidgets.QSlider):
    pass


class _WheelBlocker(QtCore.QObject):
    """설치된 위젯에 오는 휠 이벤트를 삼킨다."""

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.Type.Wheel:
            return True
        return False


def block_wheel(widget):
    """이미 만들어진 위젯의 휠 값 변경을 막는다. 위젯을 그대로 돌려준다.

    필터를 위젯의 자식으로 만들어 수명을 위젯에 맡긴다 — 지역 변수로 두면
    함수가 끝나는 순간 회수되어 **필터가 조용히 사라진다.**
    """
    widget.installEventFilter(_WheelBlocker(widget))
    widget.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
    return widget


# --- 5. 작업자 배정 드롭다운 위젯 (Assets/Rigging 공용) ---
class WorkerComboWidget(NoWheelComboBox):
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

        # 휠로 값이 바뀌지 않는 것은 NoWheelComboBox 가 맡는다.
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

    def _on_changed(self, _idx):
        if self._loading:
            return
        value = self.currentData()
        self.save_callback(value if value is not None else "-")


class NaCheckWidget(QtWidgets.QWidget):
    """'이 단계는 해당 없음' 체크 상자 (표 한 칸에 들어가는 가운데 정렬 체크박스).

    리깅하지 않는 소품, 텍스쳐를 쓰지 않는 프록시처럼 **애초에 할 일이 아닌**
    단계를 표시한다. 켜면 그 단계는 진행률 분모에서 빠지고 화면에 '해당없음'으로
    나온다. 폴더·파일은 그대로이므로 끄면 원래대로 돌아온다.

    is_na    : 현재 상태
    callback : 새 상태(bool)를 받아 저장하는 콜백
    """
    def __init__(self, is_na, callback):
        super().__init__()
        self.callback = callback

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.box = QtWidgets.QCheckBox()
        self.box.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.box.setToolTip("이 단계는 하지 않는 작업입니다 — 진행률 계산에서 빠집니다")
        # 콜백 연결 전에 초기값을 넣는다 — 화면을 그리는 것만으로 저장이 일어나면
        # 새로고침 때마다 메타 파일이 다시 쓰인다.
        self.box.setChecked(bool(is_na))
        self.box.toggled.connect(self._on_toggled)

        layout.addStretch()
        layout.addWidget(self.box)
        layout.addStretch()

    def _on_toggled(self, checked):
        self.callback(bool(checked))


# --- 6. 스토리보드 (아직 작업하지 않은 컷의 미리보기) ---
# 애니 작업 전 컷은 플레이블라스트가 없어 리뷰 탭에서 볼 것이 없다. 스토리보드
# 이미지를 컷에 배정해 두면 지정한 프레임 길이만큼 스틸을 대신 보여준다.
#
#   이미지 : {PROJ}/storyboard/{SC}/{SC}_{C}_01.jpg   (컷당 여러 장 가능)
#   프레임 : _metadata/shots/{SC}/{C}/cut_config.json  → {"frames": 50}
#   fps    : _pipeline/project_config.json 의 "fps" (없으면 24)
#
# 이미지를 shots/ 트리 **밖**(프로젝트 루트)에 두는 이유: 컷 스캔 대상 폴더가
# 아니므로 RESERVED_DIRS를 건드릴 필요가 없고, 스캐너가 컷으로 오인할 여지도 없다.
# 프레임을 assignment.json에 섞지 않는 이유: 배정 파일에 성격이 다른 키를 섞으면
# 아티스트 재저장·PD 조작이 서로를 덮는다(같은 계열의 과거 사고).
STORYBOARD_DIR_NAME = "storyboard"
STORYBOARD_TRASH_NAME = "_trash"
STORYBOARD_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
CUT_CONFIG_NAME = "cut_config.json"
DEFAULT_FPS = 24
DEFAULT_CUT_FRAMES = 50


def storyboard_root(project_path):
    return os.path.join(project_path, STORYBOARD_DIR_NAME)


def storyboard_scene_dir(project_path, scene):
    return os.path.join(project_path, STORYBOARD_DIR_NAME, scene)


def storyboard_trash_dir(project_path):
    """지운 스토리보드를 옮겨 두는 자리. 영구 삭제는 하지 않는다."""
    return os.path.join(project_path, STORYBOARD_DIR_NAME, STORYBOARD_TRASH_NAME)


def is_storyboard_image(filename):
    return filename.lower().endswith(STORYBOARD_EXTS)


def _storyboard_pattern(scene, cut):
    """`{SC}_{C}_01` 과 순번 없는 `{SC}_{C}` 를 함께 받는다.

    컷 이름을 정규식에 그대로 넣으면 안 된다(이름에 `.`·`(`가 들어갈 수 있다).
    이름 전체를 앵커로 묶어 비교하므로 C001이 C0010을 잡는 일이 없다.
    """
    return re.compile(r"^%s_%s(?:_(\d+))?$" % (re.escape(scene), re.escape(cut)),
                      re.IGNORECASE)


def storyboard_index(project_path, scene, cuts):
    """씬 폴더를 **한 번만** 조회해 {컷: [이미지 경로]} 를 만든다.

    컷마다 storyboard_files 를 부르면 같은 폴더를 컷 수만큼 다시 조회한다.
    클라우드 드라이브에서는 폴더 조회 한 번이 비싸므로, 컷 목록이 있을 때는
    이쪽을 쓴다(리뷰 탭이 씬 단위로 그리는 경로).
    """
    scene_dir = storyboard_scene_dir(project_path, scene)
    files, _dirs = _scan_dir_entries(scene_dir)
    images = [n for n in files if is_storyboard_image(n)]

    result = {}
    for cut in cuts:
        pattern = _storyboard_pattern(scene, cut)
        found = []
        for name in images:
            m = pattern.match(os.path.splitext(name)[0])
            if not m:
                continue
            idx = int(m.group(1)) if m.group(1) else 0
            found.append((idx, name.lower(), os.path.join(scene_dir, name)))
        found.sort()
        result[cut] = [p for _i, _n, p in found]
    return result


def storyboard_files(project_path, scene, cut):
    """그 컷에 배정된 스토리보드 이미지 경로를 순번대로. 없으면 []."""
    return storyboard_index(project_path, scene, [cut])[cut]


def storyboard_frame_spans(count, frames):
    """이미지 count 장을 총 frames 프레임에 균등 분배한다. 나머지는 앞쪽부터.

        (3, 50) -> [17, 17, 16]

    이미지가 프레임보다 많으면 한 장에 1프레임씩 준다 — 0프레임짜리 장면을
    만들면 그 이미지는 있는데도 화면에 뜨지 않는다.
    """
    count = int(count or 0)
    if count <= 0:
        return []
    frames = max(int(frames or 0), count)
    base, extra = divmod(frames, count)
    return [base + (1 if i < extra else 0) for i in range(count)]


def next_storyboard_index(project_path, scene, cut):
    """그 컷에 이미지를 **추가**할 때 쓸 다음 순번(1부터)."""
    pattern = _storyboard_pattern(scene, cut)
    scene_dir = storyboard_scene_dir(project_path, scene)
    highest = 0
    files, _dirs = _scan_dir_entries(scene_dir)
    for name in files:
        if not is_storyboard_image(name):
            continue
        m = pattern.match(os.path.splitext(name)[0])
        if m:
            highest = max(highest, int(m.group(1)) if m.group(1) else 1)
    return highest + 1


def storyboard_target_path(project_path, scene, cut, index, ext):
    """규약 이름으로 저장할 전체 경로. 원본 파일명은 여기서 버려진다.

    원본 이름을 그대로 두지 않는 것은 매칭을 파일명 하나로 끝내기 위해서이고,
    덤으로 한글·공백·특수문자가 파이프라인 안으로 들어오지 않는다.
    """
    ext = ext if ext.startswith(".") else "." + ext
    name = f"{scene}_{cut}_{str(index).zfill(2)}{ext.lower()}"
    return os.path.join(storyboard_scene_dir(project_path, scene), name)


# --- 6-1. 컷 프레임 (cut_config.json) ---
def cut_config_path(project_path, scene, cut):
    return os.path.join(project_path, "_metadata", "shots", scene, cut, CUT_CONFIG_NAME)


def load_cut_frames(project_path, scene, cut, default=None):
    """컷에 지정된 총 프레임 수. 지정된 적 없으면 default(기본 None)."""
    data = load_json(cut_config_path(project_path, scene, cut))
    value = data.get("frames")
    try:
        frames = int(value)
    except (TypeError, ValueError):
        return default
    return frames if frames > 0 else default


def save_cut_frames(project_path, scene, cut, frames):
    """컷의 총 프레임 수를 저장한다. frames가 0 이하/None이면 지정을 해제한다."""
    path = cut_config_path(project_path, scene, cut)
    try:
        value = int(frames)
    except (TypeError, ValueError):
        value = 0
    return save_json_key(path, "frames", value if value > 0 else None)


def project_fps(project_path):
    """프로젝트 fps. project_config.json에 없으면 24(마야 film 기준)."""
    config = load_json(os.path.join(project_path, "_pipeline", "project_config.json"))
    try:
        fps = float(config.get("fps"))
    except (TypeError, ValueError):
        return DEFAULT_FPS
    return fps if fps > 0 else DEFAULT_FPS


# --- 6-2. 파일 이름에서 컷 추측 (업로드 창의 자동 채움) ---
def guess_cut_from_filename(filename, cut_keys):
    """파일 이름에 든 토큰으로 (씬, 컷)을 추측한다. 확실하지 않으면 None.

    cut_keys: [(씬, 컷), ...] — 프로젝트에 실제로 있는 컷 목록.

    토큰 단위로만 비교한다. 부분문자열로 보면 `C001`이 `C0010`을 잡는다.
    컷 이름이 여러 씬에 겹치는데 파일 이름에 씬이 없으면 **추측하지 않는다** —
    틀린 자동 배정은 없는 것만 못하다(순서 자동배정을 하지 않는 이유와 같다).
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    tokens = {t.upper() for t in _TOKEN_SPLIT.split(stem) if t}
    if not tokens:
        return None

    matches = [(s, c) for (s, c) in cut_keys if c.upper() in tokens]
    if not matches:
        return None
    with_scene = [(s, c) for (s, c) in matches if s.upper() in tokens]
    if len(with_scene) == 1:
        return with_scene[0]
    if len(matches) == 1:
        return matches[0]
    return None