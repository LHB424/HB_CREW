"""ProjectScanner — 파이프라인 폴더/메타데이터를 읽어 진행 상태를 판정하는 스캐너.

이 파일은 **HB PD와 HB CREW가 함께 쓰는 공유 파일**이다.
원본은 HB_PD(`dist/scanner_core.py`)이고, CREW 쪽 사본은 동기화 스크립트로 가져간다.
CREW 사본을 직접 고치면 두 프로그램의 상태 판정이 조용히 갈라진다 — 반드시 여기서 고칠 것.
"""
import os
import re
import json

from common_utils import logger, is_shot_dir, is_work_scene

# 파일명 끝의 버전 토큰(`..._v012.ma`). 이름 중간에 v가 또 있어도 마지막 것이 버전이다.
_VERSION_RE = re.compile(r"_v(\d+)", re.IGNORECASE)


def latest_version(dir_path, exts, work_only=True):
    """폴더 안 파일들의 `_v###` 중 가장 큰 값을 'v012' 형태로 반환. 없으면 ''.

    상태(Done/In Progress)와 짝을 이뤄 화면에 'PUB(v003)'처럼 표기하기 위한 값이다.
    판정에는 쓰지 않는다 — 버전이 없어도 상태는 그대로 나와야 한다.
    """
    if not os.path.isdir(dir_path):
        return ""
    best = None
    try:
        names = os.listdir(dir_path)
    except Exception:
        return ""
    for f in names:
        if not f.lower().endswith(exts):
            continue
        if work_only and not is_work_scene(f):
            continue
        found = _VERSION_RE.findall(os.path.splitext(f)[0])
        if not found:
            continue
        num = int(found[-1])
        if best is None or num > best:
            best = num
    return "v%03d" % best if best is not None else ""


class ProjectScanner:
    def __init__(self, project_path):
        self.project_path = project_path
        self.cached_assets = None
        self.cached_shots = None
        self.cached_lighting = None

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
        if not os.path.isdir(tex_dir):
            return ("Not Started", "")
        exts = ('.png', '.jpg', '.jpeg', '.tga', '.tif', '.tiff', '.exr', '.bmp', '.psd')
        try:
            for f in os.listdir(tex_dir):
                if f.lower().endswith(exts):
                    # 텍스쳐는 씬 파일이 아니므로 setup/base/init 필터를 적용하지 않는다.
                    return ("Done", latest_version(tex_dir, exts, work_only=False))
        except Exception:
            pass
        return ("Not Started", "")

    def _read_user_field(self, meta_dir, json_name):
        """지정한 json 파일 하나에서 'user' 값을 읽어 반환. 실패 시 '-'."""
        try:
            with open(os.path.join(meta_dir, json_name), 'r', encoding='utf-8') as f:
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
        없거나 읽기 실패 시 빈 dict."""
        path = os.path.join(meta_dir, "assignment.json")
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            from common_utils import logger
            logger.warning(f"assignment.json 로드 실패 ({path}): {e}")
            return {}

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
        }
        if not os.path.exists(meta_dir):
            return result

        # assignment.json 1회 파싱 (작업자 오버라이드 + 마감일/완료 플래그)
        assignment = self._read_assignment(meta_dir)
        # 공용 완료(구버전 호환)
        result["DeadlineDone"] = bool(assignment.get("deadline_done", False))

        # 디렉터리 목록 1회
        try:
            all_files = os.listdir(meta_dir)
        except Exception:
            all_files = []
        json_files = sorted(f for f in all_files
                            if f.endswith('.json') and f != "assignment.json")

        # 열어본 파일 캐시 (같은 파일 중복 오픈 방지)
        cache = {}
        def load(fname):
            if fname not in cache:
                try:
                    with open(os.path.join(meta_dir, fname), 'r', encoding='utf-8') as f:
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

        (성능이 중요한 스캔 루프에서는 scan_asset_meta 를 쓰고, 이 메서드는
         다른 탭이 단건으로 물을 때를 위해 유지한다.)
        """
        if not os.path.exists(meta_dir): return "-"

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

        # 2) 버전 파일 fallback
        json_files = [f for f in os.listdir(meta_dir) if f.endswith('.json')]
        json_files = [f for f in json_files if f != "assignment.json"]
        if stage:
            json_files = [f for f in json_files if f"_{stage}_" in f]
        if not json_files: return "-"
        json_files.sort()  # zero-pad된 _v### 덕분에 파일명 정렬 = 버전 순
        return self._read_user_field(meta_dir, json_files[-1])

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
        json_files = [f for f in os.listdir(meta_dir)
                      if f.endswith('.json') and f != "assignment.json"]
        if not json_files: return ""
        json_files.sort(key=lambda x: os.path.getmtime(os.path.join(meta_dir, x)))
        latest_json = os.path.join(meta_dir, json_files[-1])
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
        
        has_pub = False
        if os.path.exists(pub_dir):
            has_pub = any(f.endswith(('.ma', '.mb')) for f in os.listdir(pub_dir))
            
        has_real_wip = False
        if not has_pub and os.path.exists(wip_dir):
            for f in os.listdir(wip_dir):
                if f.endswith(('.ma', '.mb')) and is_work_scene(f):
                    has_real_wip = True
                    break
                        
        if has_pub: actual_status = "Done"
        elif has_real_wip: actual_status = "In Progress"
        else: actual_status = "Not Started"

        meta_dir = os.path.join(self.project_path, "_metadata", "shots", scene, cut)
        if not os.path.exists(meta_dir): return actual_status
        json_files = [f for f in os.listdir(meta_dir) if f.endswith('.json')]
        if not json_files: return actual_status
        
        json_files.sort(key=lambda x: os.path.getmtime(os.path.join(meta_dir, x)))
        latest_json = os.path.join(meta_dir, json_files[-1])
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
            if not os.path.exists(cat_path): continue
            for asset_name in os.listdir(cat_path):
                asset_path = os.path.join(cat_path, asset_name)
                if not os.path.isdir(asset_path): continue
                meta_path = os.path.join(meta_assets_dir, category, asset_name)
                # 폴더를 한 번만 훑어 작업자/마감/완료를 모두 확보 (디스크 I/O 최소화)
                m = self.scan_asset_meta(meta_path)
                mdl_stat, mdl_ver = self.check_status_ver(os.path.join(asset_path, "MDL"))
                rig_stat, rig_ver = self.check_status_ver(os.path.join(asset_path, "RIG"))
                tex_stat, tex_ver = self.check_tex_status_ver(asset_path)
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
                }
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

        for seq_name in sorted(os.listdir(shots_dir)):
            seq_path = os.path.join(shots_dir, seq_name)
            if not os.path.isdir(seq_path): continue
            if not is_shot_dir(seq_name): continue  # 예약 폴더 제외
            
            if seq_name not in scene_deadlines:
                dl, done = self.get_scene_config(seq_name)
                scene_deadlines[seq_name] = dl
                scene_dones[seq_name] = done

            for cut_name in sorted(os.listdir(seq_path)):
                cut_path = os.path.join(seq_path, cut_name)
                if not os.path.isdir(cut_path): continue
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

        def has_files(path):
            if not os.path.exists(path): return False
            for f in os.listdir(path):
                if f.lower().endswith(exts):
                    if is_work_scene(f):
                        return True
            return False

        pub_scenes = os.path.join(task_base_path, "pub", sub, "scenes")
        wip_scenes = os.path.join(task_base_path, "wip", sub, "scenes")
        if has_files(pub_scenes): return ("Done", latest_version(pub_scenes, exts))
        elif has_files(wip_scenes): return ("In Progress", latest_version(wip_scenes, exts))
        else: return ("Not Started", "")

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

        for seq_name in sorted(os.listdir(shots_dir)):
            seq_path = os.path.join(shots_dir, seq_name)
            if not os.path.isdir(seq_path): continue
            if not is_shot_dir(seq_name): continue  # 예약 폴더 제외

            if seq_name not in scene_deadlines:
                dl, done = self.get_scene_config(seq_name)
                scene_deadlines[seq_name] = dl
                scene_dones[seq_name] = done

            for cut_name in sorted(os.listdir(seq_path)):
                cut_path = os.path.join(seq_path, cut_name)
                if not os.path.isdir(cut_path): continue
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
                }
        return lgt_status
