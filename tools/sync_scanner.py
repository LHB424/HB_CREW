"""스캐너 코어 동기화 — 원본은 HB_PD 저장소.

`scanner_core.py`(ProjectScanner)와 `common_utils.py`는 HB PD와 HB CREW가
**같은 내용을 써야 하는 공유 파일**이다. 상태 판정이나 데이터 계약이 한쪽에서만
바뀌면 두 프로그램이 같은 프로젝트를 다르게 읽고, 그 어긋남은 화면에 조용히 나타난다.

규칙: 고칠 일이 생기면 **HB_PD 에서 고치고** 이 스크립트로 가져온다.
CREW 쪽 사본은 직접 편집하지 않는다.

    python tools/sync_scanner.py --check   # 다른지만 확인(다르면 종료코드 1)
    python tools/sync_scanner.py           # HB_PD → HB_CREW 복사

원본 위치는 환경변수 HB_PD_DIST 로 바꿀 수 있다(다른 PC에서 경로가 다를 때).
"""
import os
import sys
import shutil
import hashlib

SHARED_FILES = ["scanner_core.py", "common_utils.py"]

SOURCE_DIR = os.environ.get("HB_PD_DIST", r"C:\HB_PD\dist")
TARGET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "dist")


def digest(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    check_only = "--check" in sys.argv

    if not os.path.isdir(SOURCE_DIR):
        print(f"[실패] 원본 폴더가 없습니다: {SOURCE_DIR}")
        print("       HB_PD_DIST 환경변수로 경로를 지정할 수 있습니다.")
        return 2

    drift = 0
    for name in SHARED_FILES:
        src = os.path.join(SOURCE_DIR, name)
        dst = os.path.join(TARGET_DIR, name)
        if not os.path.exists(src):
            print(f"[실패] 원본 파일 없음: {src}")
            return 2

        if digest(src) == digest(dst):
            print(f"[동일] {name}")
            continue

        drift += 1
        if check_only:
            print(f"[다름] {name} — sync_scanner.py 로 갱신이 필요합니다.")
        else:
            os.makedirs(TARGET_DIR, exist_ok=True)
            shutil.copy2(src, dst)
            print(f"[복사] {name} ← {SOURCE_DIR}")

    if check_only and drift:
        return 1
    print("동기화 상태 정상." if not drift else f"{drift}개 파일을 갱신했습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
