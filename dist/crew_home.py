"""HB CREW 홈 화면 — 내가 누구인지 보여주고 작업 화면으로 보내준다.

화면만 담당한다. 프로젝트를 고르거나 스캔하는 일은 하지 않고,
버튼을 누르면 시그널만 올려보낸다(crew_ui.CrewWindow 가 받는다).
**crew_ui 를 여기서 import 하지 않는다** — import 는 한 방향으로만 흐른다.
"""
import os
import random

from PySide6 import QtWidgets, QtCore, QtGui

NAME_PLACEHOLDER = "- 이름 선택 -"

# 이어서 하기 카드의 썸네일 크기(16:9). SAVER 가 남긴 .jpg 를 줄여서 넣는다.
THUMB_W, THUMB_H = 160, 90

# 켤 때마다 하나를 골라 쓴다. {name} 자리에 이름이 들어간다.
GREETINGS = (
    "안녕하세요, {name}님",
    "반갑습니다, {name}님",
    "오늘도 화이팅입니다, {name}님",
    "어서 오세요, {name}님",
    "오늘도 좋은 작업 되세요, {name}님",
    "좋은 하루 보내세요, {name}님",
    "오늘도 수고 많으십니다, {name}님",
    "함께해 주셔서 고맙습니다, {name}님",
)


def pick_greeting(previous=None):
    """인사 문구를 하나 고른다(순수 함수).

    직전에 쓴 문구는 후보에서 뺀다 — 두 번 연속 같은 인사가 나오면
    '랜덤'이 아니라 '고정 문구'처럼 보인다.
    """
    pool = [g for g in GREETINGS if g != previous] or list(GREETINGS)
    return random.choice(pool)


def format_parts(profile):
    """팀원 정보에서 파트 표시 문자열을 만든다(순수 함수).

    main_part 가 본파트, sub_part 는 보조. 둘 다 비어 있는 팀원이 흔한데
    (PJB는 세부팀 소속 팀원 대부분이 그렇다) 그때는 세부팀 이름이 사실상
    파트다('캐릭터 모델링', '텍스처'). 그것도 없을 때만 안내 문구를 준다.
    """
    if not profile:
        return "—"
    main = [p for p in profile.get("main_part", []) if p]
    sub = [p for p in profile.get("sub_part", []) if p]
    if main and sub:
        return "{} (보조: {})".format(" · ".join(main), " · ".join(sub))
    if main:
        return " · ".join(main)
    if sub:
        return "보조: " + " · ".join(sub)
    subteams = [t for t in profile.get("subteams", []) if t]
    if subteams:
        return " · ".join(subteams)
    return "파트 미지정"


class HomeView(QtWidgets.QWidget):
    """홈 화면."""

    open_tasks = QtCore.Signal()
    open_project_requested = QtCore.Signal()
    name_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._greeting = GREETINGS[0]
        self.init_ui()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        layout.setSpacing(10)

        # 이름·파트·소속은 처음 한 번 확인하는 정보라 위로 올린다.
        # 가운데 자리는 매일 달라지는 것('이어서 하기')이 갖는다.
        top = QtWidgets.QHBoxLayout()
        self.project_label = QtWidgets.QLabel("프로젝트 없음")
        self.project_label.setObjectName("ProjectLabel")
        top.addWidget(self.project_label)
        top.addStretch()
        self.name_combo = QtWidgets.QComboBox()
        self.name_combo.setMinimumWidth(150)
        self.name_combo.addItem(NAME_PLACEHOLDER)
        self.name_combo.currentIndexChanged.connect(self._on_name_changed)
        top.addWidget(self.name_combo)
        self.btn_open = QtWidgets.QPushButton("프로젝트 열기")
        self.btn_open.clicked.connect(self.open_project_requested.emit)
        top.addWidget(self.btn_open)
        layout.addLayout(top)

        layout.addStretch(1)

        # 인사
        self.greeting_label = QtWidgets.QLabel("")
        self.greeting_label.setObjectName("Greeting")
        self.greeting_label.setAlignment(QtCore.Qt.AlignCenter)
        self.greeting_label.setWordWrap(True)
        layout.addWidget(self.greeting_label)

        self.subtitle_label = QtWidgets.QLabel("")
        self.subtitle_label.setObjectName("StatusLabel")
        self.subtitle_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.subtitle_label)

        layout.addSpacing(14)

        # 이어서 하기 — 이 화면의 주인공.
        # 잡히지 않으면(첫 작업·남이 마지막으로 저장) 통째로 숨긴다. 빈 카드는
        # '아직 못 읽었다'와 '없다'를 구분해 주지 못한다.
        self.recent_card = QtWidgets.QFrame()
        self.recent_card.setObjectName("RecentCard")
        # 폭을 잡아 두지 않으면 카드가 글자 길이만큼만 줄어들어 메모가 세 줄로 접힌다.
        self.recent_card.setFixedWidth(500)
        self.recent_card.setVisible(False)
        rc = QtWidgets.QHBoxLayout(self.recent_card)
        rc.setContentsMargins(16, 14, 18, 14)
        rc.setSpacing(14)

        self.recent_thumb = QtWidgets.QLabel()
        self.recent_thumb.setObjectName("RecentThumb")
        self.recent_thumb.setFixedSize(THUMB_W, THUMB_H)
        self.recent_thumb.setAlignment(QtCore.Qt.AlignCenter)
        self.recent_thumb.setScaledContents(False)
        rc.addWidget(self.recent_thumb)

        texts = QtWidgets.QVBoxLayout()
        texts.setSpacing(3)
        self.recent_caption = QtWidgets.QLabel("이어서 하기")
        self.recent_caption.setObjectName("RecentCaption")
        self.recent_title = QtWidgets.QLabel("")
        self.recent_title.setObjectName("RecentTitle")
        self.recent_title.setWordWrap(True)
        self.recent_sub = QtWidgets.QLabel("")
        self.recent_sub.setObjectName("StatusLabel")
        self.recent_memo = QtWidgets.QLabel("")
        self.recent_memo.setObjectName("RecentMemo")
        self.recent_memo.setWordWrap(True)
        for w in (self.recent_caption, self.recent_title,
                  self.recent_sub, self.recent_memo):
            texts.addWidget(w)
        texts.addStretch()
        rc.addLayout(texts, 1)

        recent_row = QtWidgets.QHBoxLayout()
        recent_row.addStretch()
        recent_row.addWidget(self.recent_card)
        recent_row.addStretch()
        layout.addLayout(recent_row)

        layout.addSpacing(12)

        # 작업 화면으로
        self.btn_tasks = QtWidgets.QPushButton("작업")
        self.btn_tasks.setObjectName("BigButton")
        self.btn_tasks.setMinimumSize(200, 48)
        self.btn_tasks.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_tasks.clicked.connect(self.open_tasks.emit)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self.btn_tasks)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.summary_label = QtWidgets.QLabel("")
        self.summary_label.setObjectName("StatusLabel")
        self.summary_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.summary_label)

        layout.addStretch(2)

        self.footer_label = QtWidgets.QLabel(
            "읽기 전용 — 배정과 마감은 PD가 관리합니다.")
        self.footer_label.setObjectName("FooterLabel")
        layout.addWidget(self.footer_label)

    # --- 밖에서 부르는 것 ---
    def set_greeting_template(self, template):
        """이 실행에서 쓸 인사 문구를 정한다(이름이 바뀌어도 문구는 그대로)."""
        self._greeting = template

    def set_project(self, name, path):
        self.project_label.setText(name or "프로젝트 없음")
        self.project_label.setToolTip(path or "")

    def set_names(self, names, current):
        """이름 콤보를 채운다. 저장해 둔 이름이 명단에 없어도 항목으로 남긴다."""
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        self.name_combo.addItem(NAME_PLACEHOLDER)
        for nm in names:
            self.name_combo.addItem(nm)
        if current:
            idx = self.name_combo.findText(current)
            if idx < 0:
                self.name_combo.addItem(current)
                idx = self.name_combo.findText(current)
            self.name_combo.setCurrentIndex(idx)
        self.name_combo.blockSignals(False)

    def set_profile(self, my_name, profile, has_project):
        """인사·파트·소속을 갱신한다.

        profile 은 crew_ui.read_member_profile 의 결과(없으면 None).
        """
        if not has_project:
            self.greeting_label.setText("HB CREW")
            self.subtitle_label.setText("프로젝트 폴더를 먼저 선택해주세요.")
        elif not my_name:
            self.greeting_label.setText("환영합니다")
            self.subtitle_label.setText("오른쪽 위에서 내 이름을 선택해주세요.")
        elif not profile:
            self.greeting_label.setText(self._greeting.format(name=my_name))
            self.subtitle_label.setText("팀원 명단에 없는 이름입니다.")
        else:
            # 파트·소속은 카드 세 줄을 차지할 정보가 아니라 이름 밑 한 줄이다.
            self.greeting_label.setText(self._greeting.format(name=my_name))
            teams = [t for t in profile.get("teams", []) if t]
            bits = [format_parts(profile)]
            if teams:
                bits.append(" · ".join(teams))
            self.subtitle_label.setText("  ·  ".join(bits))

        self.btn_tasks.setEnabled(bool(has_project and my_name))

    def set_recent(self, title, subtitle, memo, thumb_path):
        """'이어서 하기' 카드를 채운다. title 이 비면 카드를 숨긴다."""
        if not title:
            self.recent_card.setVisible(False)
            return
        self.recent_title.setText(title)
        self.recent_sub.setText(subtitle)
        self.recent_memo.setText(memo)
        self.recent_memo.setVisible(bool(memo))

        pix = QtGui.QPixmap(thumb_path) if thumb_path and os.path.exists(thumb_path) \
            else QtGui.QPixmap()
        if pix.isNull():
            # 썸네일이 없는 저장분도 흔하다 — 자리는 남기고 안내만 흐리게.
            self.recent_thumb.setPixmap(QtGui.QPixmap())
            self.recent_thumb.setText("미리보기 없음")
        else:
            self.recent_thumb.setText("")
            self.recent_thumb.setPixmap(pix.scaled(
                THUMB_W, THUMB_H, QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation))
        self.recent_card.setVisible(True)

    def set_summary(self, text):
        self.summary_label.setText(text)

    # --- 내부 ---
    def _on_name_changed(self, _idx):
        text = self.name_combo.currentText()
        self.name_changed.emit("" if text == NAME_PLACEHOLDER else text.strip())
