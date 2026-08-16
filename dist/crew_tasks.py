"""HB CREW 작업 화면 — 내 작업 목록(읽기 전용).

이 모듈은 **화면과 순수 계산만** 갖는다. 스캐너도, 설정 파일도 모르고,
받은 행 목록을 그리기만 한다(crew_ui.CrewWindow 가 데이터를 넣어준다).
**crew_ui 를 여기서 import 하지 않는다** — import 는 한 방향으로만 흐른다.

collect_my_tasks / group_rows_by_urgency 는 Qt 창 없이 도는 순수 함수라
그대로 단위 테스트할 수 있다.
"""
from PySide6 import QtWidgets, QtCore, QtGui

from common_utils import status_label, STATUS_NA

# 배정을 읽어올 단계. 에셋은 단계별 작업자 키가 따로 있고,
# 샷(ANI)/라이팅(LGT)은 스캔 결과의 "Worker" 하나뿐이다.
ASSET_STAGES = ("MDL", "RIG", "TEX")

# 화면에 쓸 단계 이름. JSON/파일명 토큰(MDL/RIG/TEX/ANI/LGT)은 그대로 두고
# 표시만 바꾼다 — 토큰은 폴더명·스캐너와 공유하는 값이라 건드리면 안 된다.
STAGE_LABELS = {"MDL": "모델링", "RIG": "리깅", "TEX": "텍스처",
                "ANI": "애니메이션", "LGT": "라이팅"}

# 긴급도 그룹 — 아침에 여는 화면의 순서다.
# "무슨 종류의 일인가(단계)"가 아니라 "언제까지인가"로 묶는다.
# 작업자는 모델링→리깅→애니 순으로 일하지 않고 마감 순으로 일한다.
URG_LATE, URG_SOON, URG_LATER, URG_DONE = "LATE", "SOON", "LATER", "DONE"
URGENCY_GROUPS = (
    (URG_LATE, "늦었거나 오늘"),
    (URG_SOON, "이번 주"),
    (URG_LATER, "나중"),
    (URG_DONE, "끝냈음"),
)
SOON_DAYS = 7   # 며칠 앞까지를 '이번 주'로 볼지

# 마감 표시 색 — PD의 DeadlineWidget과 같은 어휘를 쓴다.
DL_NORMAL, DL_SOON, DL_OVER, DL_DONE, DL_NONE = (
    "#e8e8ec", "#F59E0B", "#EF4444", "#10B981", "#6f6f78")

# 그룹 헤더 색 — 마감 색과 같은 어휘를 쓴다(빨강=늦음, 주황=임박, 초록=완료).
GROUP_COLORS = {URG_LATE: DL_OVER, URG_SOON: DL_SOON,
                URG_LATER: "#b8b8c2", URG_DONE: DL_DONE}


# ── 필터·묶기 (순수 함수 — Qt 창 없이 동작) ─────────────────────
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
            # PD가 '해당 없음'으로 표시한 단계는 내 할 일이 아니다.
            if info.get(stage) == STATUS_NA:
                continue
            rows.append({
                "kind": "에셋",
                "group": info.get("Category", ""),
                "name": asset_name,
                "stage": stage,
                "status": info.get(stage, "Not Started"),
                "ver": info.get(f"{stage}_Ver", ""),
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
            "ver": info.get("ani_Ver", ""),
            "deadline": info.get("Deadline", ""),
            "done": bool(info.get("DeadlineDone", False)),
            "detail": "",
        })

    # 라이팅 — 마야/블렌더를 따로 판정하므로 세부는 툴팁으로 남긴다
    for shot_key in sorted(lighting):
        info = lighting[shot_key]
        if not mine(info.get("Worker")):
            continue
        if info.get("DetailedStatus") == STATUS_NA:   # 라이팅하지 않는 컷
            continue
        seq = shot_key.split("/")[0].strip()
        rows.append({
            "kind": "샷",
            "group": seq,
            "name": shot_key,
            "stage": "LGT",
            "status": info.get("DetailedStatus", "Not Started"),
            "ver": info.get("Ver", ""),
            "deadline": info.get("Deadline", ""),
            "done": bool(info.get("DeadlineDone", False)),
            "detail": "마야 {} / 블렌더 {}".format(
                status_label(info.get("Maya", ""), info.get("Maya_Ver", "")),
                status_label(info.get("Blender", ""), info.get("Blender_Ver", ""))),
        })

    return rows


def days_to_deadline(deadline):
    """마감까지 남은 날 수. 마감이 없거나 날짜 형식이 아니면 None.

    None 과 0 은 다르다 — 0 은 '오늘까지', None 은 '마감이 안 잡혔다'다.
    두 경우를 섞으면 마감 없는 일이 오늘 마감으로 올라온다.
    """
    date = QtCore.QDate.fromString((deadline or "").strip(), "yyyy-MM-dd")
    if not date.isValid():
        return None
    return QtCore.QDate.currentDate().daysTo(date)


def urgency_of(row):
    """이 작업이 들어갈 긴급도 그룹 키.

    완료 판정은 **`done`(PD가 체크한 마감 완료)만** 본다.
    상태가 pub 인 것은 '일단 냈다'일 뿐이고 리테이크가 올 수 있어 완료가 아니다.
    """
    if row.get("done"):
        return URG_DONE
    days = days_to_deadline(row.get("deadline"))
    if days is None:
        return URG_LATER
    if days <= 0:
        return URG_LATE      # 지났거나 오늘
    if days <= SOON_DAYS:
        return URG_SOON
    return URG_LATER


def _progress_rank(row):
    """손이 가야 할 순서. 작을수록 위.

    마감이 안 잡힌 작업이 많은 프로젝트에서는(PJB 실측 93%) 마감만으로 줄을
    세울 수 없다. 그럴 때 작업자가 실제로 먼저 보는 것은 **손대던 것**이다.
    """
    status = (row.get("status") or "").strip()
    if not status or status == "Not Started":
        return 1
    if status == "Done":
        return 2   # 퍼블리시까지 했는데 완료 체크만 안 된 것 — 손 갈 일이 적다
    return 0       # In Progress 와 세부 상태(Layout/Blocking/Spline/Polishing/Previs)


def _sort_key(row):
    """마감이 가까운 순. 마감이 없는 일은 뒤로 보내고, 그 안은 진행 중인 것부터."""
    days = days_to_deadline(row.get("deadline"))
    if days is None:
        return (1, 0, _progress_rank(row), row.get("name", ""))
    return (0, days, _progress_rank(row), row.get("name", ""))


def group_rows_by_urgency(rows):
    """행을 긴급도 그룹으로 묶고 각 그룹 안을 마감 순으로 세운다. Qt 창 없이 동작한다.

    반환: [(key, label, [row, ...]), ...] — URGENCY_GROUPS 순서 그대로.
    **빈 그룹도 0건으로 남긴다.** "급한 건 없다"는 것도 아침에 필요한 답이고,
    그룹이 통째로 사라지면 스캔이 덜 된 것인지 정말 없는 것인지 구분되지 않는다.
    """
    buckets = {key: [] for key, _ in URGENCY_GROUPS}
    for row in rows:
        buckets[urgency_of(row)].append(row)
    for items in buckets.values():
        items.sort(key=_sort_key)
    return [(key, label, buckets[key]) for key, label in URGENCY_GROUPS]


def progress_summary(rows):
    """한 줄 요약. 아침에 알고 싶은 건 총 건수가 아니라 '급한 게 있나'다.

    그래서 '내 작업 182건'처럼 창고 재고를 세지 않고, 급한 것부터 말한다.
    """
    if not rows:
        return "배정된 작업이 없습니다."
    counts = {key: len(items) for key, _, items in group_rows_by_urgency(rows)}
    late, soon = counts[URG_LATE], counts[URG_SOON]
    left = late + soon + counts[URG_LATER]
    if not left:
        return "남은 일이 없습니다."
    if late:
        return f"급한 일 {late}건 · 남은 일 {left}건"
    if soon:
        return f"이번 주 {soon}건 · 남은 일 {left}건"
    return f"남은 일 {left}건"


def deadline_display(deadline, done):
    """마감일을 남은 날짜 표기와 색으로 바꾼다. 반환 (텍스트, 색).

    날짜 자체보다 '며칠 남았나'가 팀원이 실제로 보는 값이다.
    완료 표시는 D값보다 우선한다(지난 마감이라도 끝냈으면 빨갛지 않아야 한다).
    """
    text, color = "—", DL_NONE
    days = days_to_deadline(deadline)   # 날짜 해석은 한 곳에서만 한다
    if days is not None:
        if days > 0:
            text, color = f"D-{days}", (DL_SOON if days <= 3 else DL_NORMAL)
        elif days == 0:
            text, color = "D-Day", DL_OVER
        else:
            text, color = f"D+{-days}", DL_OVER
    if done:
        text = f"✓ 완료 ({text})" if days is not None else "✓ 완료"
        color = DL_DONE
    return text, color


# ── 화면 ────────────────────────────────────────────────────────
class TaskView(QtWidgets.QWidget):
    """내 작업 목록 화면. 데이터는 밖에서 넣어준다(show_rows)."""

    go_home = QtCore.Signal()
    refresh_requested = QtCore.Signal()

    # '구분'(에셋/샷)은 그룹 헤더가 대신하므로 열에서 뺐다.
    COLUMNS = ["이름", "단계", "상태", "마감"]

    def __init__(self, parent=None):
        super().__init__(parent)
        # 사용자가 접어 둔 그룹 키 — 새로고침해도 유지한다.
        # 끝낸 일은 **처음부터 접혀 있다.** 아침에 여는 화면에서 이미 끝낸 일은
        # 정보가 아니라 소음이고, 지금은 그게 목록의 절반을 차지한다.
        self._collapsed = {URG_DONE}
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        top.setSpacing(8)

        self.btn_home = QtWidgets.QPushButton("‹  홈")
        self.btn_home.clicked.connect(self.go_home.emit)
        top.addWidget(self.btn_home)

        self.title_label = QtWidgets.QLabel("내 작업")
        self.title_label.setObjectName("ViewTitle")
        top.addWidget(self.title_label)

        top.addStretch()

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setObjectName("StatusLabel")
        top.addWidget(self.status_label)

        self.btn_refresh = QtWidgets.QPushButton("새로고침")
        self.btn_refresh.setObjectName("PrimaryButton")
        self.btn_refresh.clicked.connect(self.refresh_requested.emit)
        top.addWidget(self.btn_refresh)
        layout.addLayout(top)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setColumnCount(len(self.COLUMNS))
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setRootIsDecorated(True)     # 그룹 접기/펴기 화살표
        self.tree.setIndentation(14)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.tree.itemExpanded.connect(self._on_group_toggled)
        self.tree.itemCollapsed.connect(self._on_group_toggled)
        header = self.tree.header()
        # 남는 폭은 이름 열이 먹는다(기본값인 '마지막 열 늘리기'는 꺼야 한다 —
        # 켜 두면 마감 열만 넓어져 상태와 마감 사이가 휑하게 벌어진다).
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for col in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeToContents)
        layout.addWidget(self.tree, 1)

        self.footer_label = QtWidgets.QLabel(
            "읽기 전용 — 배정과 마감은 PD가 관리합니다.")
        self.footer_label.setObjectName("FooterLabel")
        layout.addWidget(self.footer_label)

    # --- 밖에서 부르는 것 ---
    def set_status(self, text):
        self.status_label.setText(text)

    def set_busy(self, busy):
        self.btn_refresh.setEnabled(not busy)

    def clear(self):
        self.tree.clear()

    def show_rows(self, rows):
        """행 목록을 긴급도 그룹으로 묶어 그린다."""
        self.tree.clear()
        for key, label, group_rows in group_rows_by_urgency(rows):
            group = self._add_group(key, label, len(group_rows))
            for row in group_rows:
                group.addChild(self._make_row(row))

    # --- 내부 ---
    def _on_group_toggled(self, item):
        """접힘 상태를 기억한다(새로고침 때 다시 채워도 유지되도록)."""
        key = item.data(0, QtCore.Qt.UserRole)
        if not key:
            return
        if item.isExpanded():
            self._collapsed.discard(key)
        else:
            self._collapsed.add(key)

    def _add_group(self, key, label, count):
        """그룹 헤더 줄. 이름 열 하나로 늘려 쓰고, 건수를 함께 보여준다."""
        group = QtWidgets.QTreeWidgetItem(self.tree, [f"{label} ({count})"])
        group.setData(0, QtCore.Qt.UserRole, key)
        # 헤더는 선택 대상이 아니다(클릭은 접기/펴기 용도).
        group.setFlags(QtCore.Qt.ItemIsEnabled)
        group.setFirstColumnSpanned(True)   # 트리에 넣은 뒤에 호출해야 먹는다
        font = group.font(0)
        font.setBold(True)
        group.setFont(0, font)
        # 0건이면 흐리게 — 급한 게 없다는 것도 답이지만 눈에 걸릴 필요는 없다.
        group.setForeground(0, QtGui.QBrush(QtGui.QColor(
            GROUP_COLORS.get(key, "#b8b8c2") if count else "#5f5f68")))
        group.setBackground(0, QtGui.QBrush(QtGui.QColor("#20202a")))
        group.setExpanded(count > 0 and key not in self._collapsed)
        return group

    def _make_row(self, row):
        item = QtWidgets.QTreeWidgetItem([
            row["name"],
            # 그룹이 더 이상 단계를 말해주지 않으므로 모든 행에 단계를 적는다.
            STAGE_LABELS.get(row["stage"], row["stage"]),
            # 표시는 PUB(v003)/WIP(v012). 필터·정렬은 row["status"] 토큰으로 한다.
            status_label(row["status"], row.get("ver", "")),
            "",
        ])
        text, color = deadline_display(row["deadline"], row["done"])
        item.setText(3, text)
        item.setForeground(3, QtGui.QBrush(QtGui.QColor(color)))
        item.setTextAlignment(3, QtCore.Qt.AlignCenter)
        item.setTextAlignment(1, QtCore.Qt.AlignCenter)

        tip = row["group"]
        if row["detail"]:
            tip = f"{tip} · {row['detail']}" if tip else row["detail"]
        if tip:
            for col in range(len(self.COLUMNS)):
                item.setToolTip(col, tip)
        return item
