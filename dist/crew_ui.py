"""HB CREW 화면 — 내 작업 목록(읽기 전용).

진입점은 crew_main.py 이고, 이 모듈은 창(CrewWindow)과 그에 딸린 로직만 갖는다.
**반대 방향(여기서 crew_main 을 import)은 하지 않는다** — 런처가 crew_main 을
"__main__" 으로 실행하기 때문에, 역방향 import 는 같은 파일을 두 번 로드해
설정/로깅이 이중으로 초기화된다.

이 프로그램이 프로젝트 폴더에 쓰는 것은 **아무것도 없다.**
기억하는 것은 사용자 설정(crew_settings.json: 마지막 프로젝트, 내 이름)뿐이다.
배정과 마감을 바꾸는 일은 PD 대시보드의 몫이다.
"""
import os
import json

from PySide6 import QtWidgets, QtCore, QtGui

from common_utils import safe_json_save, logger, user_config_path
from scanner_core import ProjectScanner

# CREW 는 처음부터 사용자 프로필 아래에 쓴다(이관해 올 옛 파일이 없다).
SETTINGS_FILE = user_config_path("crew", "crew_settings.json")

CONFIG_REL = os.path.join("_pipeline", "project_config.json")

# 배정을 읽어올 단계. 에셋은 단계별 작업자 키가 따로 있고,
# 샷(ANI)/라이팅(LGT)은 스캔 결과의 "Worker" 하나뿐이다.
ASSET_STAGES = ("MDL", "RIG", "TEX")

NAME_PLACEHOLDER = "- 이름 선택 -"


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


def read_project_name(path):
    """표시용 프로젝트 이름. 설정에 이름이 없으면 폴더명을 쓴다."""
    try:
        with open(os.path.join(path, CONFIG_REL), 'r', encoding='utf-8') as f:
            name = json.load(f).get("project_name", "").strip()
            if name:
                return name
    except Exception:
        pass
    return os.path.basename(os.path.normpath(path)) if path else ""


# ── 필터 (순수 함수 — Qt 없이 동작) ─────────────────────────────
def collect_my_tasks(assets, shots, lighting, my_name):
    """스캔 결과에서 **내 이름이 배정된 작업만** 골라 표에 넣을 행 목록으로 만든다.

    이름 비교는 앞뒤 공백만 떼고 **정확히 일치(==)** 시킨다.
    부분 일치를 쓰면 '김민'이 '김민수'의 작업까지 가져가고,
    상태 판정에서 같은 실수로 났던 오탐과 원인이 같아진다.

    반환: [{kind, group, name, stage, status, deadline, done, detail}, ...]
      kind   : "에셋" | "샷"
      group  : 에셋은 카테고리(Character/Env/Prop), 샷은 시퀀스명
      stage  : MDL/RIG/TEX/ANI/LGT
      detail : 부가 설명(툴팁용). 없으면 ""
    """
    rows = []
    me = (my_name or "").strip()
    if not me:
        return rows

    def mine(value):
        return (value or "").strip() == me

    # 에셋 — 단계별 담당자가 따로 있다(assignment.json 우선, 없으면 최신 버전 JSON)
    for asset_name in sorted(assets):
        info = assets[asset_name]
        for stage in ASSET_STAGES:
            if not mine(info.get(f"Worker_{stage}")):
                continue
            rows.append({
                "kind": "에셋",
                "group": info.get("Category", ""),
                "name": asset_name,
                "stage": stage,
                "status": info.get(stage, "Not Started"),
                "deadline": info.get(f"Deadline_{stage}", "") or info.get("Deadline", ""),
                "done": bool(info.get(f"DeadlineDone_{stage}", info.get("DeadlineDone", False))),
                "detail": "",
            })

    # 샷 애니메이션 — 키는 "{SC} / {C}", 마감은 씬 단위
    for shot_key in sorted(shots):
        info = shots[shot_key]
        if not mine(info.get("Worker")):
            continue
        seq = shot_key.split("/")[0].strip()
        rows.append({
            "kind": "샷",
            "group": seq,
            "name": shot_key,
            "stage": "ANI",
            "status": info.get("DetailedStatus") or info.get("ani", "Not Started"),
            "deadline": info.get("Deadline", ""),
            "done": bool(info.get("DeadlineDone", False)),
            "detail": "",
        })

    # 라이팅 — 마야/블렌더를 따로 판정하므로 세부는 툴팁으로 남긴다
    for shot_key in sorted(lighting):
        info = lighting[shot_key]
        if not mine(info.get("Worker")):
            continue
        seq = shot_key.split("/")[0].strip()
        rows.append({
            "kind": "샷",
            "group": seq,
            "name": shot_key,
            "stage": "LGT",
            "status": info.get("DetailedStatus", "Not Started"),
            "deadline": info.get("Deadline", ""),
            "done": bool(info.get("DeadlineDone", False)),
            "detail": f"마야 {info.get('Maya', '-')} / 블렌더 {info.get('Blender', '-')}",
        })

    return rows


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
            s.cached_assets = None
            s.cached_shots = None
            s.cached_lighting = None
            assets = s.scan_assets()
            shots = s.scan_shots()
            lighting = s.scan_lighting()
            self.data_ready.emit(assets, shots, lighting)
        except Exception as e:
            logger.error(f"스캔 실패: {e}")
            self.data_ready.emit({}, {}, {})


# ── 창 ──────────────────────────────────────────────────────────
class CrewWindow(QtWidgets.QWidget):
    COLUMNS = ["구분", "이름", "단계", "상태", "마감"]

    def __init__(self, project_path=None):
        super().__init__()
        self.project_path = project_path if is_valid_project(project_path) else None
        self.scanner = None
        self.thread = None
        self._refresh_pending = False
        self.assets = {}
        self.shots = {}
        self.lighting = {}
        self.my_name = (load_settings().get("my_name") or "").strip()

        self.setMinimumSize(760, 520)
        self.resize(920, 640)
        self.init_ui()
        self.apply_styles()
        self.update_title()

        if self.project_path:
            self.load_project(self.project_path)
        else:
            self.status_label.setText("프로젝트를 선택하세요.")

    # --- UI 구성 ---
    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(8)

        self.project_label = QtWidgets.QLabel("프로젝트 없음")
        self.project_label.setObjectName("ProjectLabel")
        top.addWidget(self.project_label)

        self.btn_open = QtWidgets.QPushButton("프로젝트 열기")
        self.btn_open.clicked.connect(self.choose_project)
        top.addWidget(self.btn_open)

        top.addSpacing(12)
        top.addWidget(QtWidgets.QLabel("내 이름"))
        self.name_combo = QtWidgets.QComboBox()
        self.name_combo.setMinimumWidth(150)
        self.name_combo.addItem(NAME_PLACEHOLDER)
        self.name_combo.currentIndexChanged.connect(self.on_name_changed)
        top.addWidget(self.name_combo)

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_refresh.setObjectName("PrimaryButton")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)

        top.addStretch()
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("StatusLabel")
        top.addWidget(self.status_label)
        layout.addLayout(top)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        header = self.tree.header()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.tree, 1)

        self.footer_label = QtWidgets.QLabel(
            "읽기 전용 — 배정과 마감은 PD가 관리합니다.")
        self.footer_label.setObjectName("FooterLabel")
        layout.addWidget(self.footer_label)

    def apply_styles(self):
        self.setStyleSheet("""
            QWidget { background-color: #1a1a1e; color: #e8e8ec;
                      font-family: 'Segoe UI', 'Malgun Gothic'; font-size: 10pt; }
            QLabel#ProjectLabel { font-size: 12pt; font-weight: bold; color: #ffffff; }
            QLabel#StatusLabel { color: #8b8b94; }
            QLabel#FooterLabel { color: #6f6f78; font-size: 9pt; }
            QPushButton { background-color: #2a2a30; color: #d4d4dc; border: 1px solid #3a3a42;
                          border-radius: 4px; padding: 6px 12px; }
            QPushButton:hover { background-color: #3a3a40; }
            QPushButton#PrimaryButton { background-color: #6366f1; color: #ffffff;
                          border: none; font-weight: bold; }
            QPushButton#PrimaryButton:hover { background-color: #818cf8; }
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
        self.project_label.setText(read_project_name(path))
        self.project_label.setToolTip(path)
        self.reload_member_names()
        self.refresh()

    def reload_member_names(self):
        """팀원 명단(project_config.json)으로 이름 콤보를 다시 채운다.
        저장해 둔 내 이름이 명단에 없어도(외주·이름 변경 등) 항목으로 남겨 유지한다."""
        names = self.scanner.get_all_member_names() if self.scanner else []
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        self.name_combo.addItem(NAME_PLACEHOLDER)
        for nm in names:
            self.name_combo.addItem(nm)
        if self.my_name:
            idx = self.name_combo.findText(self.my_name)
            if idx < 0:
                self.name_combo.addItem(self.my_name)
                idx = self.name_combo.findText(self.my_name)
            self.name_combo.setCurrentIndex(idx)
        self.name_combo.blockSignals(False)

    def on_name_changed(self, _idx):
        text = self.name_combo.currentText()
        self.my_name = "" if text == NAME_PLACEHOLDER else text.strip()
        save_settings(my_name=self.my_name)
        self.update_title()
        self.populate()

    # --- 스캔 ---
    def refresh(self):
        if not self.scanner:
            return
        if self.thread and self.thread.isRunning():
            # 실행 중인 QThread 를 다시 띄우거나 파괴하면 크래시가 난다. 끝난 뒤로 미룬다.
            self._refresh_pending = True
            return
        self.btn_refresh.setEnabled(False)
        self.status_label.setText("스캔 중…")
        self.thread = CrewScannerThread(self.scanner)
        self.thread.data_ready.connect(self.on_scan_complete)
        self.thread.start()

    def on_scan_complete(self, assets, shots, lighting):
        self.assets, self.shots, self.lighting = assets, shots, lighting
        self.btn_refresh.setEnabled(True)
        self.populate()
        if self._refresh_pending:
            self._refresh_pending = False
            self.refresh()

    # --- 표시 ---
    def populate(self):
        self.tree.clear()
        if not self.scanner:
            self.status_label.setText("프로젝트를 선택하세요.")
            return
        if not self.my_name:
            self.status_label.setText("이름을 선택하세요.")
            return

        rows = collect_my_tasks(self.assets, self.shots, self.lighting, self.my_name)
        for row in rows:
            item = QtWidgets.QTreeWidgetItem([
                row["kind"],
                row["name"],
                row["stage"],
                row["status"],
                row["deadline"] or "-",
            ])
            tip = row["group"]
            if row["detail"]:
                tip = f"{tip} · {row['detail']}" if tip else row["detail"]
            if tip:
                for col in range(len(self.COLUMNS)):
                    item.setToolTip(col, tip)
            self.tree.addTopLevelItem(item)

        self.status_label.setText(f"내 작업 {len(rows)}건")

    # --- 종료 ---
    def closeEvent(self, event):
        if self.thread and self.thread.isRunning():
            self.thread.wait(3000)
        super().closeEvent(event)
