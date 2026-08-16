"""HB CREW '이어서 하기' — 내가 마지막으로 저장한 작업 하나를 찾는다.

컴퓨터를 켜고 처음 드는 생각은 "오늘 뭐 하지"가 아니라 **"어제 뭐 하다 말았지"**다.
SAVER 가 저장할 때마다 남긴 버전 기록(`user`·`date`·`description`)과 썸네일이
이미 프로젝트에 쌓여 있는데, 지금까지 그걸 읽는 프로그램이 없었다.

Qt 를 쓰지 않는다 — 창 없이 그대로 단위 테스트할 수 있고,
스캔 스레드 안에서 돌려도 UI 를 붙잡지 않는다.

**`scanner_core.py` 를 고쳐서 넣지 않는다.** 그 파일은 hb_pd 가 원본인 사본이라
CREW 전용 기능을 넣으면 다음 동기화 때 지워진다. `crew_ui.read_member_profile`
이 여기 있는 것과 같은 이유다.
"""
import os
import json
import datetime

from common_utils import latest_version_meta, meta_place_of, logger

# 저장 기록이 있을 수 없는 상태 — 폴더를 열어 볼 필요가 없다.
NO_WORK_STATUS = ("", "Not Started")

# 버전 기록 JSON 이 쓰는 시각 형식. SAVER 가 이 형식으로 쓴다.
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ── 경로 ────────────────────────────────────────────────────────
def meta_dir_of(project_path, row):
    """작업 행이 가리키는 메타 폴더. 규약 밖 이름이면 None.

    샷은 `_metadata/shots/{SC}/{C}`, 에셋은 `_metadata/assets/{종류}/{에셋}`.
    스캐너가 쓰는 경로와 같은 규약이다. → 데이터 계약
    """
    if not project_path:
        return None
    name = (row.get("name") or "").strip()
    if row.get("kind") == "샷":
        parts = [p.strip() for p in name.split("/")]
        if len(parts) != 2 or not all(parts):
            return None
        return os.path.join(project_path, "_metadata", "shots", parts[0], parts[1])
    category = (row.get("group") or "").strip()
    if not (category and name):
        return None
    return os.path.join(project_path, "_metadata", "assets", category, name)


def _read_meta(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _version_of(meta_path):
    """메타 파일 이름에서 (버전, wip|pub). 규약 밖 이름이면 ("", "")."""
    base = os.path.splitext(os.path.basename(meta_path))[0]
    place = meta_place_of(base)
    if not place:
        return "", ""
    return base.rsplit("_", 1)[-1], place[1]


def _fallback_date(path):
    """JSON 에 `date` 가 없는 옛 기록은 파일 시각으로 대신한다."""
    try:
        return datetime.datetime.fromtimestamp(
            os.path.getmtime(path)).strftime(DATE_FORMAT)
    except OSError:
        return ""


# ── 찾기 ────────────────────────────────────────────────────────
def find_recent_work(project_path, rows, my_name):
    """내가 마지막으로 저장한 작업 하나. 없으면 None.

    반환 {row, date, description, version, kind, thumb}

    - 행마다 메타 폴더의 **가장 최근 버전 기록 하나만** 본다. 그 기록의 작성자가
      내가 아니면 건너뛴다 — 배정은 나여도 마지막으로 만진 사람이 남일 수 있고,
      그 작업을 "이어서 하기"로 내밀면 남의 작업을 이어받으라는 말이 된다.
    - 아직 아무도 저장하지 않은 작업(Not Started)은 폴더를 열어 보지 않는다.
    - **어디서 실패해도 None 이지 예외가 아니다.** 이 카드가 안 뜨는 것과
      프로그램이 죽는 것은 전혀 다른 일이다.
    """
    me = (my_name or "").strip()
    if not (project_path and rows and me):
        return None

    best = None
    for row in rows:
        if (row.get("status") or "").strip() in NO_WORK_STATUS:
            continue
        meta_dir = meta_dir_of(project_path, row)
        if not meta_dir:
            continue
        try:
            meta_path = latest_version_meta(meta_dir, row.get("stage"))
        except Exception as e:
            logger.warning(f"최근 작업 조회 실패({meta_dir}): {e}")
            continue
        if not meta_path:
            continue
        data = _read_meta(meta_path)
        if data is None or (data.get("user") or "").strip() != me:
            continue

        # 형식이 같은 문자열이라 사전순 비교가 곧 시간순 비교다.
        date = (data.get("date") or "").strip() or _fallback_date(meta_path)
        if best and date <= best["date"]:
            continue

        version, kind = _version_of(meta_path)
        thumb = os.path.splitext(meta_path)[0] + ".jpg"
        best = {
            "row": row,
            "date": date,
            "description": (data.get("description") or "").strip(),
            "version": version,
            "kind": kind,
            "thumb": thumb if os.path.exists(thumb) else "",
        }
    return best


# ── 표시 ────────────────────────────────────────────────────────
def humanize_date(date_text, today=None):
    """저장 시각을 사람이 읽는 말로. 규약 밖 문자열은 그대로 돌려준다.

    아침에 알고 싶은 것은 '2026-06-06 12:37:41'이 아니라 '그제'다.
    """
    text = (date_text or "").strip()
    if not text:
        return ""
    try:
        when = datetime.datetime.strptime(text, DATE_FORMAT)
    except ValueError:
        return text

    today = today or datetime.date.today()
    days = (today - when.date()).days
    if days == 0:
        return when.strftime("오늘 %H:%M")
    if days == 1:
        return "어제"
    if days == 2:
        return "그제"
    if 3 <= days <= 6:
        return f"{days}일 전"
    if when.year == today.year:
        return f"{when.month}/{when.day}"
    return f"{when.year}. {when.month}. {when.day}"


def describe(recent, stage_labels=None):
    """카드에 넣을 (제목, 부제, 메모). recent 가 None 이면 세 칸 다 빈 문자열.

    단계 이름표는 인자로 받는다 — 여기서 crew_tasks 를 import 하면 이 모듈이
    Qt 에 딸려 들어가고, 창 없이 테스트할 수 있다는 성질이 사라진다.
    """
    if not recent:
        return "", "", ""
    row = recent["row"]
    token = row.get("stage", "")
    stage = (stage_labels or {}).get(token, token)
    title = f"{row.get('name', '')}  ·  {stage}"

    bits = []
    if recent["version"]:
        bits.append(f"{recent['kind'] or 'wip'} {recent['version']}")
    when = humanize_date(recent["date"])
    if when:
        bits.append(when)
    return title, "  ·  ".join(bits), recent["description"]
