"""HB CREW 창(셸) — 설정·프로젝트·스캔을 맡고 화면 둘을 갈아 끼운다.

진입점은 crew_main.py 이고, 이 모듈은 창(CrewWindow)과 그에 딸린 로직만 갖는다.
**반대 방향(여기서 crew_main 을 import)은 하지 않는다** — 런처가 crew_main 을
"__main__" 으로 실행하기 때문에, 역방향 import 는 같은 파일을 두 번 로드해
설정/로깅이 이중으로 초기화된다.

화면은 각자 파일에 있다. import 는 한 방향으로만 흐른다:
    crew_main → crew_ui → (crew_home, crew_tasks, crew_recent)
                        → (common_utils, scanner_core)
화면에서 창으로 올라오는 요청은 import 가 아니라 **시그널**로 전달된다.

이 프로그램이 프로젝트 폴더에 쓰는 것은 **아무것도 없다.**
기억하는 것은 사용자 설정(crew_settings.json: 마지막 프로젝트, 내 이름, 직전 인사)뿐이다.
배정과 마감을 바꾸는 일은 PD 대시보드의 몫이다.
"""
import os
import json

from PySide6 import QtWidgets, QtCore

from common_utils import safe_json_save, logger, user_config_path
from scanner_core import ProjectScanner
from crew_home import HomeView, pick_greeting
from crew_tasks import TaskView, collect_my_tasks, progress_summary, STAGE_LABELS
from crew_recent import find_recent_work, describe

# CREW 는 처음부터 사용자 프로필 아래에 쓴다(이관해 올 옛 파일이 없다).
SETTINGS_FILE = user_config_path("crew", "crew_settings.json")

CONFIG_REL = os.path.join("_pipeline", "project_config.json")

PAGE_HOME, PAGE_TASKS = 0, 1


# ── 설정 ────────────────────────────────────────────────────────
def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception as e:
            logger.warning(f"crew_settings.json 읽기 실패: {e}")
    return {}


def save_settings(**changes):
    """기존 값을 보존한 채 넘어온 키만 갱신한다."""
    data = load_settings()
    data.update(changes)
    safe_json_save(SETTINGS_FILE, data)
    return data


# ── 프로젝트 ────────────────────────────────────────────────────
def is_valid_project(path):
    """파이프라인 프로젝트 폴더인지 여부(project_config.json 존재)."""
    return bool(path) and os.path.exists(os.path.join(path, CONFIG_REL))


def _read_config(path):
    try:
        with open(os.path.join(path, CONFIG_REL), 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_project_name(path):
    """표시용 프로젝트 이름. 설정에 이름이 없으면 폴더명을 쓴다."""
    name = _read_config(path).get("project_name", "").strip() if path else ""
    if name:
        return name
    return os.path.basename(os.path.normpath(path)) if path else ""


def read_member_profile(path, name):
    """project_config.json 에서 이 사람의 파트·소속을 모은다. 없으면 None.

    한 사람이 여러 팀/세부팀에 들어가 있는 경우가 흔하다(예: 관리팀 + PRE).
    그래서 파트는 **모든 소속의 합집합**으로 모으고, 소속은 "팀 › 세부팀"으로 적는다.
    이름 비교는 앞뒤 공백만 떼고 **정확히 일치(==)** — 부분 일치를 쓰면
    '김민'이 '김민수'의 소속을 가져간다(작업 필터와 같은 규칙).

    이 파일은 hb_pd 와 공유하는 scanner_core 가 아니라 여기서 읽는다.
    scanner_core 는 원본이 hb_pd 에 있는 사본이라, CREW 전용 기능을 넣으면
    동기화할 때 지워진다.
    """
    me = (name or "").strip()
    if not (path and me):
        return None

    data = _read_config(path)
    teams = data.get("teams", [])
    if not teams and "members" in data:
        teams = [{"members": data.get("members", [])}]

    found = False
    main_part, sub_part, where, subteams = [], [], [], []

    def add(dst, value):
        value = (value or "").strip()
        if value and value not in dst:
            dst.append(value)

    def collect(members, label, subteam_name=None):
        nonlocal found
        for m in members or []:
            if not isinstance(m, dict) or (m.get("name", "") or "").strip() != me:
                continue
            found = True
            for p in (m.get("main_part") or []):
                add(main_part, p)
            for p in (m.get("sub_part") or []):
                add(sub_part, p)
            add(where, label)
            add(subteams, subteam_name)

    for team in teams:
        if not isinstance(team, dict):
            continue
        team_name = (team.get("team_name", "") or "").strip()
        collect(team.get("members", []), team_name)
        for subteam in team.get("subteams", []) or []:
            if not isinstance(subteam, dict):
                continue
            sub_name = (subteam.get("subteam_name", "") or "").strip()
            collect(subteam.get("members", []),
                    f"{team_name} › {sub_name}" if team_name and sub_name else (sub_name or team_name),
                    sub_name)

    if not found:
        return None
    # subteams 는 파트가 비었을 때의 대안이다 — PJB처럼 세부팀 이름이
    # 사실상 파트인 프로젝트('캐릭터 모델링', '텍스처')가 많다.
    return {"main_part": main_part, "sub_part": sub_part,
            "teams": where, "subteams": subteams}


# ── 스캔 스레드 ─────────────────────────────────────────────────
class CrewScannerThread(QtCore.QThread):
    """스캔은 처음부터 백그라운드에서 한다.

    네트워크/클라우드 드라이브에서는 폴더 훑기가 수 초씩 걸려, UI 스레드에서
    돌리면 창이 통째로 얼어붙는다. 캐시는 이 스레드에서 비우고 결과는 시그널로만
    넘긴다(스캐너 객체의 캐시에 기대지 않는다).
    """
    data_ready = QtCore.Signal(dict, dict, dict)

    def __init__(self, scanner):
        super().__init__()
        self.scanner = scanner

    def run(self):
        try:
            s = self.scanner
            s.clear_cache()   # 비우는 책임은 스캐너에 있다(캐시가 늘어도 빠뜨리지 않게)
            assets = s.scan_assets()
            shots = s.scan_shots()
            lighting = s.scan_lighting()
            self.data_ready.emit(assets, shots, lighting)
        except Exception as e:
            logger.error(f"스캔 실패: {e}")
            self.data_ready.emit({}, {}, {})


class CrewRecentThread(QtCore.QThread):
    """'이어서 하기' 찾기도 백그라운드에서 한다.

    내 작업마다 메타 폴더를 한 번씩 열어 보므로(PJB 116건 기준 0.5초, 클라우드
    드라이브에서는 더 걸린다) UI 스레드에서 돌리면 창이 그만큼 멈춘다.
    목록은 스캔이 끝나는 즉시 나오고, 카드는 뒤늦게 채워진다.

    결과에 **이름을 실어 보낸다.** 조회 중에 사용자가 이름을 바꿀 수 있고,
    그때 늦게 도착한 결과를 그대로 그리면 남의 작업이 내 카드에 뜬다.
    """
    found = QtCore.Signal(str, object)

    def __init__(self, project_path, rows, my_name):
        super().__init__()
        self.project_path = project_path
        self.rows = rows
        self.my_name = my_name

    def run(self):
        try:
            recent = find_recent_work(self.project_path, self.rows, self.my_name)
        except Exception as e:
            logger.error(f"이어서 하기 조회 실패: {e}")
            recent = None
        self.found.emit(self.my_name, recent)


# ── 창 ──────────────────────────────────────────────────────────
class CrewWindow(QtWidgets.QWidget):

    def __init__(self, project_path=None):
        super().__init__()
        settings = load_settings()
        self.project_path = project_path if is_valid_project(project_path) else None
        self.scanner = None
        self.thread = None
        self.recent_thread = None
        self._refresh_pending = False
        self._recent_pending = False
        self._recent_name = None   # 지금 카드에 떠 있는 사람
        self._scanned = False
        self.assets = {}
        self.shots = {}
        self.lighting = {}
        self.my_name = (settings.get("my_name") or "").strip()

        self.setMinimumSize(760, 520)
        self.resize(920, 640)
        self.init_ui()
        self.apply_styles()
        self.update_title()

        # 인사는 **실행할 때 한 번** 고른다(화면을 오갈 때마다 바뀌면 산만하다).
        greeting = pick_greeting(settings.get("last_greeting"))
        self.home.set_greeting_template(greeting)
        save_settings(last_greeting=greeting)

        if self.project_path:
            self.load_project(self.project_path)
        else:
            self.refresh_home()

    # --- UI 구성 ---
    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QtWidgets.QStackedWidget()
        self.home = HomeView()
        self.tasks = TaskView()
        self.stack.addWidget(self.home)     # PAGE_HOME
        self.stack.addWidget(self.tasks)    # PAGE_TASKS
        layout.addWidget(self.stack)

        self.home.open_project_requested.connect(self.choose_project)
        self.home.name_changed.connect(self.on_name_changed)
        self.home.open_tasks.connect(self.show_tasks)
        self.tasks.go_home.connect(self.show_home)
        self.tasks.refresh_requested.connect(self.refresh)

    def apply_styles(self):
        self.setStyleSheet("""
            QWidget { background-color: #1a1a1e; color: #e8e8ec;
                      font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 10pt; }
            /* 라벨 배경은 비워 둔다 — 위의 QWidget 배경이 카드 위에도 칠해져
               카드가 얼룩덜룩해진다. */
            QLabel { background-color: transparent; }
            QLabel#ProjectLabel { font-size: 12pt; font-weight: bold; color: #ffffff; }
            QLabel#ViewTitle { font-size: 12pt; font-weight: bold; color: #ffffff; }
            QLabel#Greeting { font-size: 20pt; font-weight: bold; color: #ffffff; }
            QLabel#StatusLabel { color: #8b8b94; }
            QLabel#FooterLabel { color: #6f6f78; font-size: 9pt; }
            QLabel#RecentCaption { color: #818cf8; font-size: 9pt; font-weight: bold; }
            QLabel#RecentTitle { color: #ffffff; font-size: 13pt; font-weight: bold; }
            QLabel#RecentMemo { color: #b8b8c2; font-size: 9pt; }
            QLabel#RecentThumb { background-color: #101013; border: 1px solid #2f2f38;
                          border-radius: 4px; color: #4f4f58; font-size: 8pt; }
            QFrame#RecentCard { background-color: #202027; border: 1px solid #3a3a52;
                          border-radius: 8px; }
            QPushButton { background-color: #2a2a30; color: #d4d4dc; border: 1px solid #3a3a42;
                          border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #3a3a40; }
            QPushButton#PrimaryButton { background-color: #6366f1; color: #ffffff;
                          border: none; font-weight: bold; }
            QPushButton#PrimaryButton:hover { background-color: #818cf8; }
            QPushButton#BigButton { background-color: #6366f1; color: #ffffff; border: none;
                          border-radius: 6px; font-size: 13pt; font-weight: bold; }
            QPushButton#BigButton:hover { background-color: #818cf8; }
            QPushButton#BigButton:disabled { background-color: #2a2a30; color: #6f6f78; }
            QComboBox { background-color: #242429; color: #e8e8ec; border: 1px solid #3a3a42;
                        border-radius: 4px; padding: 4px 8px; }
            QComboBox:hover { border: 1px solid #6366f1; }
            QComboBox QAbstractItemView { background-color: #1a1a1e; color: #e8e8ec;
                        selection-background-color: #6366f1; }
            QTreeWidget { background-color: #141417; alternate-background-color: #1a1a1e;
                          border: 1px solid #2a2a30; border-radius: 6px; }
            QTreeWidget::item { padding: 5px 4px; }
            QTreeWidget::item:selected { background-color: #2f2f6b; }
            QHeaderView::section { background-color: #242429; color: #b8b8c2;
                          border: none; border-bottom: 1px solid #3a3a42; padding: 6px; }
        """)

    def update_title(self):
        self.setWindowTitle(f"HB CREW | {self.my_name}" if self.my_name else "HB CREW")

    # --- 화면 이동 ---
    def show_home(self):
        self.stack.setCurrentIndex(PAGE_HOME)

    def show_tasks(self):
        if not (self.scanner and self.my_name):
            return
        self.stack.setCurrentIndex(PAGE_TASKS)
        if not self._scanned:
            self.refresh()   # 스캔 전에 들어왔으면(빠른 클릭) 여기서 시작한다

    # --- 프로젝트 ---
    def choose_project(self):
        """폴더를 고르기만 한다. 프로젝트를 만들거나 고치지 않는다."""
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "프로젝트 폴더 선택")
        if not path:
            return
        if not is_valid_project(path):
            QtWidgets.QMessageBox.warning(
                self, "프로젝트 아님",
                "파이프라인 프로젝트 폴더가 아닙니다.\n"
                f"({CONFIG_REL} 파일이 없습니다)\n\n"
                "프로젝트 최상위 폴더를 선택해주세요.")
            return
        save_settings(last_project=path)
        self.load_project(path)

    def load_project(self, path):
        self.project_path = path
        self.scanner = ProjectScanner(path)
        self._scanned = False
        self.home.set_project(read_project_name(path), path)
        self.home.set_names(self.scanner.get_all_member_names(), self.my_name)
        self.refresh_home()
        self.refresh()

    def on_name_changed(self, name):
        self.my_name = name
        save_settings(my_name=self.my_name)
        self.update_title()
        self.refresh_home()
        self.populate()

    def refresh_home(self):
        """홈의 인사·파트·소속과 작업 건수를 현재 상태로 맞춘다."""
        profile = read_member_profile(self.project_path, self.my_name)
        self.home.set_profile(self.my_name, profile, bool(self.scanner))
        self.home.set_summary(self.summary_text())

    def summary_text(self):
        if not self.scanner:
            return ""
        if not self.my_name:
            return ""
        if not self._scanned:
            return "작업을 불러오는 중…"
        return progress_summary(self.current_rows())

    # --- 스캔 ---
    def current_rows(self):
        return collect_my_tasks(self.assets, self.shots, self.lighting, self.my_name)

    def refresh(self):
        if not self.scanner:
            return
        if self.thread and self.thread.isRunning():
            # 실행 중인 QThread 를 다시 띄우거나 파괴하면 크래시가 난다. 끝난 뒤로 미룬다.
            self._refresh_pending = True
            return
        self.tasks.set_busy(True)
        self.tasks.set_status("스캔 중…")
        self.home.set_summary("작업을 불러오는 중…" if self.my_name else "")
        self.thread = CrewScannerThread(self.scanner)
        self.thread.data_ready.connect(self.on_scan_complete)
        self.thread.start()

    def on_scan_complete(self, assets, shots, lighting):
        self.assets, self.shots, self.lighting = assets, shots, lighting
        self._scanned = True
        self.tasks.set_busy(False)
        self.populate()
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()

    # --- 표시 ---
    def populate(self):
        if not self.scanner:
            self.tasks.clear()
            self.tasks.set_status("프로젝트를 선택하세요.")
            self.home.set_summary("")
            self.home.set_recent("", "", "", "")
            return
        if not self.my_name:
            self.tasks.clear()
            self.tasks.set_status("이름을 선택하세요.")
            self.home.set_summary("")
            self.home.set_recent("", "", "", "")
            return

        rows = self.current_rows()
        self.tasks.show_rows(rows)
        self.tasks.set_status(progress_summary(rows))
        self.home.set_summary(self.summary_text())
        self.find_recent(rows)

    # --- 이어서 하기 ---
    def find_recent(self, rows):
        """내가 마지막으로 저장한 작업을 백그라운드로 찾는다."""
        if not (self.project_path and self.my_name):
            return
        if self._recent_name != self.my_name:
            # 이름이 바뀐 참이다. 새 결과가 올 때까지 앞사람 카드를 남겨 두면 안 된다.
            self.home.set_recent("", "", "", "")
            self._recent_name = self.my_name
        if self.recent_thread and self.recent_thread.isRunning():
            # 실행 중인 QThread 를 다시 띄우면 크래시가 난다(스캔과 같은 규칙).
            self._recent_pending = True
            return
        self.recent_thread = CrewRecentThread(self.project_path, rows, self.my_name)
        self.recent_thread.found.connect(self.on_recent_found)
        self.recent_thread.start()

    def on_recent_found(self, name, recent):
        if name != self.my_name:
            return   # 조회 중에 이름이 바뀌었다 — 늦게 온 남의 결과는 버린다
        title, subtitle, memo = describe(recent, STAGE_LABELS)
        self.home.set_recent(title, subtitle, memo,
                             recent["thumb"] if recent else "")
        if self._recent_pending:
            self._recent_pending = False
            self.find_recent(self.current_rows())

    # --- 종료 ---
    def closeEvent(self, event):
        for thread in (self.thread, self.recent_thread):
            if thread and thread.isRunning():
                thread.wait(3000)
        super().closeEvent(event)
