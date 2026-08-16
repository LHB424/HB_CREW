"""ProjectScanner — 파이프라인 폴더/메타데이터를 읽어 진행 상태를 판정하는 스캐너.

이 파일은 **HB PD와 HB CREW가 함께 쓰는 공유 파일**이다.
원본은 HB_PD(`dist/scanner_core.py`)이고, CREW 쪽 사본은 동기화 스크립트로 가져간다.
CREW 사본을 직접 고치면 두 프로그램의 상태 판정이 조용히 갈라진다 — 반드시 여기서 고칠 것.
"""
import os
import re
import json

from common_utils import (logger, is_shot_dir, is_work_scene, is_version_meta,
                          iter_version_metas, na_stages_of, STATUS_NA)

# 파일명 끝의 버전 토큰(`..._v012.ma`). 이름 중간에 v가 또 있어도 마지막 것이 버전이다.
_VERSION_RE = re.compile(r"_v(\d+)", re.IGNORECASE)


def list_subdirs(dir_path):
    """폴더 안의 **하위 폴더 이름만** 목록으로 돌려준다(정렬하지 않음).

    `os.listdir` + 이름마다 `os.path.isdir` 을 부르던 자리를 대신한다.
    Windows에서 디렉터리 열거는 이미 각 항목의 속성을 함께 돌려주므로
    `entry.is_dir()` 은 추가 조회 없이 답한다 — 항목 수만큼의 왕복이 사라진다.
    폴더가 없거나 접근할 수 없으면 빈 목록(호출 측의 기존 동작과 같다).
    """
    try:
        with os.scandir(dir_path) as it:
            return [e.name for e in it if e.is_dir()]
    except Exception:
        return []


def list_names(dir_path):
    """폴더 안의 이름 목록. 폴더가 없거나 접근 불가면 빈 목록.

    `os.path.exists` 로 미리 확인한 뒤 `os.listdir` 을 부르던 자리를 대신한다.
    두 번 묻던 것을 한 번으로 줄이며, 확인과 열거 사이에 폴더가 사라지는
    경합에서도 예외 대신 빈 목록이 된다(판정 결과는 기존과 같다).
    """
    try:
        return os.listdir(dir_path)
    except Exception:
        return []


def scan_versioned_dir(dir_path, exts, work_only=True):
    """폴더를 **한 번만** 훑어 (해당 파일이 있는지, 최신 버전 문자열)을 함께 반환한다.

    반환: (bool, "v012") — 파일이 없거나 폴더가 없으면 (False, "").
    버전 토큰이 없는 파일만 있어도 첫 값은 True다(상태 판정은 버전과 무관).

    상태 판정과 버전 표기가 같은 폴더를 각각 열던 것을 하나로 합친 것이다.
    폴더 조회 횟수가 절반이 되며, 네트워크/클라우드 드라이브에서 차이가 크다.
    """
    try:
        names = os.listdir(dir_path)
    except Exception:
        return (False, "")   # 폴더 없음/접근 불가
    found_any = False
    best = None
    for f in names:
        if not f.lower().endswith(exts):
            continue
        if work_only and not is_work_scene(f):
            continue
        found_any = True
        found = _VERSION_RE.findall(os.path.splitext(f)[0])
        if not found:
            continue
        num = int(found[-1])
        if best is None or num > best:
            best = num
    return (found_any, "v%03d" % best if best is not None else "")


def latest_version(dir_path, exts, work_only=True):
    """폴더 안 파일들의 `_v###` 중 가장 큰 값을 'v012' 형태로 반환. 없으면 ''.

    상태(Done/In Progress)와 짝을 이뤄 화면에 'PUB(v003)'처럼 표기하기 위한 값이다.
    판정에는 쓰지 않는다 — 버전이 없어도 상태는 그대로 나와야 한다.
    """
    return scan_versioned_dir(dir_path, exts, work_only)[1]


class ProjectScanner:
    def __init__(self, project_path):
        self.project_path = project_path
        self.cached_assets = None
        self.cached_shots = None
        self.cached_lighting = None
        # 메타 폴더 1개를 여러 번 여는 것을 막는 스캔 내 메모. 키는 메타 폴더 경로.
        # 같은 컷을 scan_shots(ANI)와 scan_lighting(LGT)이 각각 훑던 왕복도 여기서 합쳐진다.
        self._meta_index_cache = {}
        self._assignment_cache = {}

    def clear_cache(self):
        """스캔 캐시를 모두 비운다 — 새로고침 전에 호출해 실측을 강제한다.

        캐시 필드를 호출하는 쪽에서 하나씩 None으로 되돌리면, 나중에 캐시가
        늘어났을 때 한쪽(HB PD / HB CREW)만 빠뜨려 옛 데이터가 남는다.
        비우는 책임은 여기 한 곳에만 둔다.
        """
        self.cached_assets = None
        self.cached_shots = None
        self.cached_lighting = None
        self._meta_index_cache.clear()
        self._assignment_cache.clear()

    def check_status(self, task_base_path):
        return self.check_status_ver(task_base_path)[0]

    def check_status_ver(self, task_base_path):
        """마야 기준 (상태, 버전) 튜플. 버전은 **현재 상태 쪽** 폴더의 최신 v###."""
        return self.check_dcc_status_ver(task_base_path, "maya")

    def check_tex_status(self, asset_path):
        return self.check_tex_status_ver(asset_path)[0]

    def check_tex_status_ver(self, asset_path):
        """텍스쳐는 wip 없이 TEX/textures 에 퍼블리시된다(tex_publisher).
        이미지 파일이 하나라도 있으면 Done, 없으면 Not Started.
        버전은 텍스쳐 파일명(`{에셋}_{파트}_basecolor_v###.png`)의 최대값."""
        tex_dir = os.path.join(asset_path, "TEX", "textures")
        exts = ('.png', '.jpg', '.jpeg', '.tga', '.tif', '.tiff', '.exr', '.bmp', '.psd')
        # 텍스쳐는 씬 파일이 아니므로 setup/base/init 필터를 적용하지 않는다.
        has_tex, ver = scan_versioned_dir(tex_dir, exts, work_only=False)
        return ("Done", ver) if has_tex else ("Not Started", "")

    def _read_user_field(self, json_path, json_name=""):
        """지정한 json 파일 하나에서 'user' 값을 읽어 반환. 실패 시 '-'.

        경로를 통째로 받는다 — 버전 메타는 `{STAGE}/{wip|pub}/` 아래에도 있어
        메타 폴더와 파일 이름만으로는 자리를 알 수 없다."""
        json_name = json_name or os.path.basename(json_path)
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                worker_name = data.get("user", "")
                if worker_name and worker_name.strip():
                    return worker_name.strip()
        except Exception as e:
            from common_utils import logger
            logger.warning(f"메타데이터 로드 실패 ({json_name}): {e}")
        return "-"

    def _read_assignment(self, meta_dir):
        """폴더의 assignment.json(PD가 지정한 단계별 담당자)을 읽어 dict 반환.
        없거나 읽기 실패 시 빈 dict. 본 파일이 깨져 있으면 직전 백업을 쓴다.

        한 번 읽은 폴더는 `clear_cache()` 전까지 다시 열지 않는다 — 같은 컷의
        assignment.json을 작업자·해당없음 판정이 각각 열어 컷당 2~3회 반복됐다.
        캐시 수명은 cached_assets/cached_shots 와 같다(새로고침마다 비워진다)."""
        if meta_dir not in self._assignment_cache:
            from common_utils import load_json
            self._assignment_cache[meta_dir] = load_json(
                os.path.join(meta_dir, "assignment.json"))
        return self._assignment_cache[meta_dir]

    def _meta_index(self, meta_dir):
        """메타 폴더의 버전 기록 JSON 색인 {파일이름: 전체경로}. 폴더당 1회만 훑는다.

        `iter_version_metas` 는 한 번 부를 때마다 메타 폴더 → `{STAGE}` →
        `{wip|pub}` 을 새로 조회한다(폴더당 3~4 왕복). 단계별로 따로 부르면
        그만큼 배가 되므로, **모든 단계를 한 번에** 받아 두고 단계 필터는
        메모리에서 건다. 같은 이름이 두 자리에 있으면 새 구조 쪽이 남는다.
        캐시 수명은 `_read_assignment` 와 같다."""
        if meta_dir not in self._meta_index_cache:
            self._meta_index_cache[meta_dir] = dict(iter_version_metas(meta_dir))
        return self._meta_index_cache[meta_dir]

    def _latest_meta(self, meta_dir, stage=None):
        """색인에서 **가장 최근에 저장된** 버전 기록 JSON의 전체 경로. 없으면 None.

        `common_utils.latest_version_meta` 와 판정 규칙(mtime 최대)이 같지만
        폴더를 다시 훑지 않고 `_meta_index` 를 쓴다. mtime 조회는 파일마다
        개별로 감싼다 — 색인을 만든 뒤 조회하기까지의 사이에 파일이 사라질 수
        있고(임시 파일·동기화 중인 클라우드 폴더), 예외가 나면 스캔 전체가 무효가 된다."""
        latest_path, latest_mtime = None, -1.0
        for name, path in self._meta_index(meta_dir).items():
            if not is_version_meta(name, stage):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_path, latest_mtime = path, mtime
        return latest_path

    def scan_asset_meta(self, meta_dir):
        """에셋 메타 폴더를 '한 번만' 훑어 필요한 값을 모두 뽑아 반환한다.

        기존에는 작업자(대표/MDL/RIG)·마감일·완료여부를 각각 다른 메서드로 읽어
        같은 폴더에 os.listdir + 파일 open 을 에셋당 8~10회 반복했다. 여기서
        디렉터리 목록 1회 + assignment.json 1회 + 단계별 최신 파일만 열어
        디스크 접근을 대폭 줄인다.

        반환: {
          "Worker": 대표작업자, "Worker_MDL": .., "Worker_RIG": ..,
          "Deadline": "yyyy-MM-dd" 또는 "", "DeadlineDone": bool
        }
        """
        result = {
            "Worker": "-", "Worker_MDL": "-", "Worker_RIG": "-", "Worker_TEX": "-",
            "Deadline": "", "DeadlineDone": False,
            "Deadline_MDL": "", "Deadline_RIG": "", "Deadline_TEX": "",
            "DeadlineDone_MDL": False, "DeadlineDone_RIG": False, "DeadlineDone_TEX": False,
            "NA_Stages": set(),
        }
        # 폴더 존재 여부를 따로 묻지 않는다 — 없으면 아래 두 읽기가 각각
        # 빈 값을 돌려주고, 결과는 위의 기본값 그대로다(기존과 동일).

        # assignment.json 1회 파싱 (작업자 오버라이드 + 마감일/완료 플래그)
        assignment = self._read_assignment(meta_dir)
        # 공용 완료(구버전 호환)
        result["DeadlineDone"] = bool(assignment.get("deadline_done", False))
        # PD가 '해당 없음'으로 표시한 단계(리깅 안 하는 소품 등)
        result["NA_Stages"] = na_stages_of(assignment)

        # 버전 기록만 남긴다 — 배정 파일과, 저장 중 잠깐 생기는 임시 파일을 뺀다.
        # (임시 파일은 이름이 tmp… 라 정렬에서 마지막에 와, 대표값 폴백을 가로챈다)
        # iter_version_metas 가 새 구조(`{STAGE}/{wip|pub}/`)와 옛 평면 구조를 함께
        # 훑는다. 같은 이름이 두 자리에 있으면 새 구조 쪽이 남는다.
        meta_paths = self._meta_index(meta_dir)             # 파일이름 → 전체경로
        json_files = sorted(meta_paths)

        # 열어본 파일 캐시 (같은 파일 중복 오픈 방지)
        cache = {}
        def load(fname):
            if fname not in cache:
                try:
                    with open(meta_paths[fname], 'r', encoding='utf-8') as f:
                        d = json.load(f)
                        cache[fname] = d if isinstance(d, dict) else {}
                except Exception:
                    cache[fname] = {}
            return cache[fname]

        def user_of(fname):
            u = load(fname).get("user", "")
            return u.strip() if u and u.strip() else "-"

        # 단계별 최신 파일
        def latest(stage):
            files = [f for f in json_files if f"_{stage}_" in f]
            return files[-1] if files else None

        mdl_file = latest("MDL")
        rig_file = latest("RIG")
        tex_file = latest("TEX")

        # 단계별 작업자: assignment 우선, 없으면 해당 단계 최신 파일의 user
        def resolve(stage, stage_file):
            ov = assignment.get(stage, "")
            if ov and ov.strip():
                return ov.strip()
            return user_of(stage_file) if stage_file else "-"

        result["Worker_MDL"] = resolve("MDL", mdl_file)
        result["Worker_RIG"] = resolve("RIG", rig_file)
        result["Worker_TEX"] = resolve("TEX", tex_file)

        # 대표 작업자: assignment(MDL>RIG>ANI) 우선, 없으면 폴더 최신 파일의 user
        rep = "-"
        for s in ("MDL", "RIG", "ANI"):
            ov = assignment.get(s, "")
            if ov and ov.strip():
                rep = ov.strip(); break
        if rep == "-" and json_files:
            rep = user_of(json_files[-1])
        result["Worker"] = rep

        # 마감일(공용): 폴더 최신 파일의 deadline (구버전 호환 fallback)
        shared_deadline = ""
        if json_files:
            shared_deadline = load(json_files[-1]).get("deadline", "")
        result["Deadline"] = shared_deadline

        # 단계별 마감일/완료: assignment.json의 단계별 값 우선, 없으면 공용값으로 fallback.
        #   deadline_MDL / deadline_RIG  (마감일)
        #   done_MDL / done_RIG          (완료 플래그)
        def stage_deadline(stage):
            v = assignment.get(f"deadline_{stage}", "")
            return v if (v and v.strip()) else shared_deadline
        def stage_done(stage):
            if f"done_{stage}" in assignment:
                return bool(assignment.get(f"done_{stage}"))
            return result["DeadlineDone"]  # 구버전 공용 완료로 fallback

        result["Deadline_MDL"] = stage_deadline("MDL")
        result["Deadline_RIG"] = stage_deadline("RIG")
        result["Deadline_TEX"] = stage_deadline("TEX")
        result["DeadlineDone_MDL"] = stage_done("MDL")
        result["DeadlineDone_RIG"] = stage_done("RIG")
        result["DeadlineDone_TEX"] = stage_done("TEX")

        return result

    def get_worker_from_metadata(self, meta_dir, stage=None):
        """작업자 이름을 메타데이터에서 읽어온다.

        읽기 우선순위:
          1) assignment.json 의 해당 단계 값(PD가 대시보드에서 지정한 담당자)
          2) 없으면 파일명 '_{stage}_' 버전 파일의 user(실제로 저장한 사람)
          3) 둘 다 없으면 "-"

        stage 가 None 이면 assignment.json 의 아무 값이나(대표), 그것도 없으면
        폴더의 모든 JSON 중 최신 파일의 user 를 본다.

        폴더 조회는 `_meta_index` 가 폴더당 1회로 묶으므로 스캔 루프에서 불러도
        된다(에셋은 값을 한꺼번에 뽑는 scan_asset_meta 쪽이 여전히 낫다).
        """
        # 폴더 존재 여부는 따로 묻지 않는다 — 폴더가 없으면 assignment.json도
        # 있을 수 없고, 아래 목록도 비어 "-"로 끝난다(기존 결과와 동일).

        # 1) PD 지정(assignment.json) 우선
        assignment = self._read_assignment(meta_dir)
        if stage:
            override = assignment.get(stage, "")
            if override and override.strip():
                return override.strip()
        else:
            # 대표값 요청: 지정된 단계 중 아무거나(MDL 우선, 다음 RIG, 그다음 ANI)
            for s in ("MDL", "RIG", "ANI"):
                override = assignment.get(s, "")
                if override and override.strip():
                    return override.strip()

        # 2) 버전 파일 fallback (새 구조·옛 평면 구조 모두)
        index = self._meta_index(meta_dir)
        names = sorted(n for n in index if is_version_meta(n, stage))
        if not names: return "-"
        # zero-pad된 _v### 덕분에 파일명 정렬 = 버전 순
        name = names[-1]
        return self._read_user_field(index[name], name)

    def get_all_member_names(self):
        """project_config.json의 teams(구버전은 members)에서 모든 팀원 이름을 모아
        중복 없이 순서대로 반환한다. 드롭다운 채우기에 사용."""
        names = []
        if not self.project_path:
            return names
        config_path = os.path.join(self.project_path, "_pipeline", "project_config.json")
        if not os.path.exists(config_path):
            return names
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except Exception:
            return names
        teams = data.get("teams", [])
        if not teams and "members" in data:
            teams = [{"members": data.get("members", [])}]
        def collect(members):
            for m in members:
                nm = m.get("name", "").strip() if isinstance(m, dict) else str(m).strip()
                if nm and nm not in names:
                    names.append(nm)
        for team in teams:
            collect(team.get("members", []))
            # 세부팀(subteams) 안의 팀원도 포함한다.
            # (기존엔 팀 직속 멤버만 모아, 세부팀에 넣은 팀원이 드롭다운에서 누락됐음)
            for subteam in team.get("subteams", []):
                collect(subteam.get("members", []))
        return names

    def get_deadline_from_metadata(self, meta_dir):
        """JSON 메타데이터에서 개별 마감일(주로 에셋)을 읽어오는 메서드"""
        if not os.path.exists(meta_dir): return ""
        latest_json = self._latest_meta(meta_dir)
        if not latest_json: return ""
        try:
            with open(latest_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("deadline", "")
        except: 
            pass
        return ""

    def get_deadline_done_from_metadata(self, meta_dir):
        """에셋의 마감 완료 여부를 assignment.json의 deadline_done에서 읽는다.
        버전 JSON과 분리돼 있어 아티스트 재저장에 영향받지 않는다."""
        assignment = self._read_assignment(meta_dir)
        return bool(assignment.get("deadline_done", False))

    def get_scene_deadline(self, scene_name):
        if not self.project_path: return ""
        scene_meta_path = os.path.join(self.project_path, "_metadata", "shots", scene_name, "scene_config.json")
        if not os.path.exists(scene_meta_path): return ""
        try:
            with open(scene_meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("deadline", "")
        except:
            return ""

    def get_scene_config(self, scene_name):
        """scene_config.json을 1회 읽어 (deadline, deadline_done)을 반환."""
        if not self.project_path: return ("", False)
        scene_meta_path = os.path.join(self.project_path, "_metadata", "shots", scene_name, "scene_config.json")
        if not os.path.exists(scene_meta_path): return ("", False)
        try:
            with open(scene_meta_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return (data.get("deadline", ""), bool(data.get("deadline_done", False)))
        except:
            return ("", False)

    def get_scene_deadline_done(self, scene_name):
        """씬 마감 완료 여부를 scene_config.json의 deadline_done에서 읽는다."""
        return self.get_scene_config(scene_name)[1]

    def get_detailed_shot_status(self, scene, cut):
        wip_dir = os.path.join(self.project_path, "shots", scene, cut, "ANI", "wip", "maya", "scenes")
        pub_dir = os.path.join(self.project_path, "shots", scene, cut, "ANI", "pub", "maya", "scenes")
        
        # pub 판정에는 setup/base/init 필터를 걸지 않는다 —
        # 버전 표기용 check_dcc_status_ver 와 규칙이 다르므로 합치면 안 된다.
        has_pub = any(f.endswith(('.ma', '.mb')) for f in list_names(pub_dir))

        has_real_wip = False
        if not has_pub:
            for f in list_names(wip_dir):
                if f.endswith(('.ma', '.mb')) and is_work_scene(f):
                    has_real_wip = True
                    break
                        
        if has_pub: actual_status = "Done"
        elif has_real_wip: actual_status = "In Progress"
        else: actual_status = "Not Started"

        meta_dir = os.path.join(self.project_path, "_metadata", "shots", scene, cut)
        # 상태(status)는 버전 기록 JSON에만 있다. 같은 폴더의 assignment.json이나
        # 저장 중 잠깐 생기는 임시 파일이 "최신"으로 뽑히면 세부 상태가 사라진다.
        latest_json = self._latest_meta(meta_dir, stage="ANI")
        if not latest_json: return actual_status
        try:
            with open(latest_json, 'r', encoding='utf-8') as f:
                data = json.load(f)
                status = data.get("status", "").strip()
                return status if status else actual_status
        except: return actual_status

    def scan_assets(self):
        if self.cached_assets is not None:
            return self.cached_assets
        asset_status = {}
        if not self.project_path: return asset_status
        assets_dir = os.path.join(self.project_path, "assets")
        meta_assets_dir = os.path.join(self.project_path, "_metadata", "assets")
        if not os.path.exists(assets_dir): return asset_status
        for category in ["Character", "Env", "Prop"]:
            cat_path = os.path.join(assets_dir, category)
            # 카테고리 폴더가 없으면 빈 목록이 되어 자연히 건너뛴다.
            for asset_name in list_subdirs(cat_path):
                asset_path = os.path.join(cat_path, asset_name)
                meta_path = os.path.join(meta_assets_dir, category, asset_name)
                # 폴더를 한 번만 훑어 작업자/마감/완료를 모두 확보 (디스크 I/O 최소화)
                m = self.scan_asset_meta(meta_path)
                mdl_stat, mdl_ver = self.check_status_ver(os.path.join(asset_path, "MDL"))
                rig_stat, rig_ver = self.check_status_ver(os.path.join(asset_path, "RIG"))
                tex_stat, tex_ver = self.check_tex_status_ver(asset_path)

                # '해당 없음' 단계는 폴더 판정을 덮어쓴다. 폴더는 그대로 두므로
                # 표시를 해제하면 원래 상태가 다시 나온다.
                na = m["NA_Stages"]
                if "MDL" in na: mdl_stat, mdl_ver = STATUS_NA, ""
                if "RIG" in na: rig_stat, rig_ver = STATUS_NA, ""
                if "TEX" in na: tex_stat, tex_ver = STATUS_NA, ""

                asset_status[asset_name] = {
                    "Category": category,
                    "Worker": m["Worker"],
                    "Worker_MDL": m["Worker_MDL"],
                    "Worker_RIG": m["Worker_RIG"],
                    "Deadline": m["Deadline"],
                    "DeadlineDone": m["DeadlineDone"],
                    "Deadline_MDL": m["Deadline_MDL"],
                    "Deadline_RIG": m["Deadline_RIG"],
                    "DeadlineDone_MDL": m["DeadlineDone_MDL"],
                    "DeadlineDone_RIG": m["DeadlineDone_RIG"],
                    "Worker_TEX": m["Worker_TEX"],
                    "Deadline_TEX": m["Deadline_TEX"],
                    "DeadlineDone_TEX": m["DeadlineDone_TEX"],
                    "MDL": mdl_stat,
                    "RIG": rig_stat,
                    "TEX": tex_stat,
                    # 화면 표기용 버전(판정에는 쓰지 않는다) — 'PUB(v003)'의 v003
                    "MDL_Ver": mdl_ver,
                    "RIG_Ver": rig_ver,
                    "TEX_Ver": tex_ver,
                    # 화면에서 체크 상태를 되살리기 위한 목록(집계는 위 상태 토큰으로 한다)
                    "NA_Stages": sorted(na),
                }
        self.cached_assets = asset_status
        return asset_status

    def scan_shots(self):
        if self.cached_shots is not None:
            return self.cached_shots
        shot_status = {}

        if not self.project_path: return shot_status
        shots_dir = os.path.join(self.project_path, "shots")
        meta_shots_dir = os.path.join(self.project_path, "_metadata", "shots")
        if not os.path.exists(shots_dir): return shot_status

        scene_deadlines = {}
        scene_dones = {}

        for seq_name in sorted(list_subdirs(shots_dir)):
            seq_path = os.path.join(shots_dir, seq_name)
            if not is_shot_dir(seq_name): continue  # 예약 폴더 제외
            
            if seq_name not in scene_deadlines:
                dl, done = self.get_scene_config(seq_name)
                scene_deadlines[seq_name] = dl
                scene_dones[seq_name] = done

            for cut_name in sorted(list_subdirs(seq_path)):
                cut_path = os.path.join(seq_path, cut_name)
                if not is_shot_dir(cut_name): continue  # _review_cache 등 예약 폴더 제외
                meta_path = os.path.join(meta_shots_dir, seq_name, cut_name)

                ani_stat, ani_ver = self.check_status_ver(os.path.join(cut_path, "ANI"))

                shot_status[f"{seq_name} / {cut_name}"] = {
                    # 샷은 ANI 단일 단계이므로 _ANI_ 파일 기준으로 작업자를 읽는다.
                    "Worker": self.get_worker_from_metadata(meta_path, stage="ANI"),
                    "Deadline": scene_deadlines[seq_name],
                    "DeadlineDone": scene_dones[seq_name],
                    "ani": ani_stat,
                    # 세부 상태(Spline 등)가 폴더 판정을 덮어써도 버전은 파일 기준이다.
                    "ani_Ver": ani_ver,
                    "DetailedStatus": self.get_detailed_shot_status(seq_name, cut_name) # 이 부분 추가!
                }
        self.cached_shots = shot_status
        return shot_status

    def check_dcc_status(self, task_base_path, dcc):
        return self.check_dcc_status_ver(task_base_path, dcc)[0]

    def check_dcc_status_ver(self, task_base_path, dcc):
        """특정 DCC(maya|blender) 기준으로 wip/pub 폴더를 보고 (상태, 버전)을 판정한다.

        경로 규약:
          maya   : {task_base}/{wip|pub}/maya/scenes/*.ma|.mb
          blender: {task_base}/{wip|pub}/blender/scenes/*.blend
        (라이팅 saver 규약과 동일. 마야 라이팅 폴더는 아직 없을 수 있으며,
         그 경우 자연히 'Not Started'로 처리된다.)

        버전은 상태를 결정한 폴더에서만 읽는다. pub이 있으면 pub의 최신 버전이며,
        그 뒤에 더 올라간 wip 버전은 보지 않는다.
        """
        if dcc == "maya":
            sub, exts = ("maya", (".ma", ".mb"))
        else:
            sub, exts = ("blender", (".blend",))

        # pub이 있으면 wip 폴더는 아예 열지 않는다(기존 판정 순서 그대로).
        pub_scenes = os.path.join(task_base_path, "pub", sub, "scenes")
        has_pub, pub_ver = scan_versioned_dir(pub_scenes, exts)
        if has_pub:
            return ("Done", pub_ver)

        wip_scenes = os.path.join(task_base_path, "wip", sub, "scenes")
        has_wip, wip_ver = scan_versioned_dir(wip_scenes, exts)
        if has_wip:
            return ("In Progress", wip_ver)
        return ("Not Started", "")

    def scan_lighting(self):
        """라이팅(LGT)을 컷 단위로 스캔한다. 마야/블렌더 상태를 각각 반환.

        반환 키: "{SC} / {C}" →
          {Worker, Deadline, DeadlineDone, Maya, Blender, DetailedStatus}
        Maya/Blender 는 각 DCC의 진행 상태(Done/In Progress/Not Started).
        """
        if self.cached_lighting is not None:
            return self.cached_lighting
        lgt_status = {}
        if not self.project_path: return lgt_status
        shots_dir = os.path.join(self.project_path, "shots")
        meta_shots_dir = os.path.join(self.project_path, "_metadata", "shots")
        if not os.path.exists(shots_dir): return lgt_status

        scene_deadlines = {}
        scene_dones = {}

        for seq_name in sorted(list_subdirs(shots_dir)):
            seq_path = os.path.join(shots_dir, seq_name)
            if not is_shot_dir(seq_name): continue  # 예약 폴더 제외

            if seq_name not in scene_deadlines:
                dl, done = self.get_scene_config(seq_name)
                scene_deadlines[seq_name] = dl
                scene_dones[seq_name] = done

            for cut_name in sorted(list_subdirs(seq_path)):
                cut_path = os.path.join(seq_path, cut_name)
                if not is_shot_dir(cut_name): continue  # _review_cache 등 예약 폴더 제외
                lgt_base = os.path.join(cut_path, "LGT")
                meta_path = os.path.join(meta_shots_dir, seq_name, cut_name)

                maya_stat, maya_ver = self.check_dcc_status_ver(lgt_base, "maya")
                blender_stat, blender_ver = self.check_dcc_status_ver(lgt_base, "blender")

                # 종합 상태: 둘 중 하나라도 Done이면 Done, 진행 중이면 In Progress
                # 종합 버전은 그 상태를 만든 DCC 쪽 버전을 따른다(마야 우선).
                if "Done" in (maya_stat, blender_stat):
                    overall = "Done"
                    overall_ver = maya_ver if maya_stat == "Done" else blender_ver
                elif "In Progress" in (maya_stat, blender_stat):
                    overall = "In Progress"
                    overall_ver = maya_ver if maya_stat == "In Progress" else blender_ver
                else:
                    overall = "Not Started"
                    overall_ver = ""

                # 라이팅을 하지 않는 컷은 PD가 '해당 없음'으로 표시한다.
                # 두 DCC와 종합 상태를 함께 덮어야 어느 화면에서도 어긋나지 않는다.
                na = na_stages_of(self._read_assignment(meta_path))
                if "LGT" in na:
                    maya_stat = blender_stat = overall = STATUS_NA
                    maya_ver = blender_ver = overall_ver = ""

                lgt_status[f"{seq_name} / {cut_name}"] = {
                    "Worker": self.get_worker_from_metadata(meta_path, stage="LGT"),
                    "Deadline": scene_deadlines[seq_name],
                    "DeadlineDone": scene_dones[seq_name],
                    "Maya": maya_stat,
                    "Blender": blender_stat,
                    "Maya_Ver": maya_ver,
                    "Blender_Ver": blender_ver,
                    "DetailedStatus": overall,
                    "Ver": overall_ver,
                    "NA_Stages": sorted(na),
                }
        self.cached_lighting = lgt_status
        return lgt_status
