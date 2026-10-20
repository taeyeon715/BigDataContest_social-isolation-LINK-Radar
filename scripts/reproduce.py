"""분석 리포트를 한 번에 다시 만든다 (REPRODUCE.md).

실행 예
  uv run python scripts/reproduce.py --dry-run          # 순서와 원본 필요 여부만 보기
  uv run python scripts/reproduce.py --no-raw           # 원본 없이 되는 단계만 (자원 원장·자원 추천 평가·M4·점검)
  uv run python scripts/reproduce.py                    # data/interim 이 있으면 전 단계 (적재는 건너뜀)
  uv run python scripts/reproduce.py --ingest           # 대회 원본 zip(BC_RAW_DIR)부터 적재까지 포함
  uv run python scripts/reproduce.py --check-hash       # 다 만든 뒤 리포트 CSV 해시를 reports/결과_해시.txt 와 비교
  uv run python scripts/reproduce.py --only r2 m3       # 몇 단계만
  uv run python scripts/reproduce.py --only check_no_raw --check-hash   # 다시 만들지 않고 해시만 검사

- 단계마다 걸린 시간과 성공 여부를 reports/재현_기록.csv 에 남긴다 (컴퓨터마다 다름, 해시 비교에서 뺌).
- 해시는 CSV 를 줄 끝(CRLF/LF)만 맞춘 뒤 sha256 으로 잰다. 그림(PNG)은 글꼴·버전에 따라 바이트가 달라져 비교하지 않는다.
- 검증 목록은 두 개다.
  - reports/결과_해시.txt: git 에 올린(제출하는) 리포트 CSV 전부. 명시적 목록이다. --check-hash 에서
    값이 다르거나, 목록의 파일이 없거나, 목록에 없는 CSV 가 reports/ 에 새로 생기면 실패(종료 코드 1).
  - reports/결과_해시_로컬.txt: 레포에 올리지 않는 로컬 전용 결과(M1 예측_전체.csv, 원천 결제건수가 있음).
    파일이 있을 때만 비교하고, 없으면 "건너뜀"으로 적는다 (새 체크아웃에는 없음).
- --write-hash 는 git 추적 및 무시되지 않은 신규 reports/**/*.csv 로 검증 목록을 다시 쓴다 (git 이 없으면 로컬 전용을 뺀 전부).
- 자원 추천 평가의 정답 세트(notebooks/w2_labels.py)는 결과를 보기 전에 고정한 입력이라 기본으로 다시 만들지 않는다 (--relabel).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HASH_FILE = ROOT / "reports" / "결과_해시.txt"
LOCAL_HASH_FILE = ROOT / "reports" / "결과_해시_로컬.txt"
LOG_FILE = ROOT / "reports" / "재현_기록.csv"
NOT_HASHED = {"재현_기록.csv"}
# 레포에 올리지 않는 로컬 전용 결과 (.gitignore). reports 기준 상대 경로
LOCAL_ONLY = {"m1_기대활동모델/예측_전체.csv"}


@dataclass(frozen=True)
class Step:
    key: str
    cmd: tuple[str, ...]
    needs: str  # "raw" = 대회 원본 zip, "interim" = 적재된 정리본(data/interim), "none" = 원본 없이 됨
    out: str
    note: str = ""


PY = ("uv", "run", "python")
STEPS: list[Step] = [
    Step("ingest", (*PY, "-m", "bigcontest.ingest"), "raw", "data/interim/*.parquet, reports/eda0_적재결과", "--ingest 일 때만"),
    Step("eda1", (*PY, "notebooks/eda1_data_quality.py"), "interim", "reports/eda1_데이터진단"),
    Step("eda2", (*PY, "notebooks/eda2_telecom_basics.py"), "interim", "reports/eda2_통신기초"),
    Step("eda3", (*PY, "notebooks/eda3_card_basics.py"), "interim", "reports/eda3_카드기초"),
    Step("w1", (*PY, "notebooks/w1_resource_ledger.py"), "none", "reports/w1_자원원장, external/resources/복지자원_원장_v2.csv"),
    Step("eda4", (*PY, "notebooks/eda4_external.py"), "interim", "reports/eda4_외부데이터결합", "eda2 다음"),
    Step("eda5", (*PY, "notebooks/eda5_insights.py"), "interim", "reports/eda5_인사이트재현", "eda4 다음"),
    Step("eda6", (*PY, "notebooks/eda6_alarm_reeval.py"), "interim", "reports/eda6_사전경보재평가"),
    Step("m1", (*PY, "notebooks/m1_expected_activity.py"), "interim", "reports/m1_기대활동모델, configs/models.yaml decisions"),
    Step("m2", (*PY, "notebooks/m2_proxy_task_b.py"), "interim", "reports/m2_대리과제B"),
    Step("r1", (*PY, "notebooks/r1_level_rules.py"), "interim", "reports/r1_등급규칙"),
    Step("w2_labels", (*PY, "notebooks/w2_labels.py"), "none", "reports/w2_자원추천평가/정답세트.csv", "--relabel 일 때만"),
    Step("w2", (*PY, "notebooks/w2_resource_eval.py"), "none", "reports/w2_자원추천평가"),
    Step("m4", (*PY, "notebooks/m4_risk_factors.py"), "none", "reports/m3_모델링결과/M4.md", "eda2·eda4·w1 CSV 사용"),
    Step("r2", (*PY, "notebooks/r2_risk_results.py"), "interim", "reports/r2_위험도결과", "r1·eda3·eda5·eda6·m1 다음"),
    Step("m3", (*PY, "notebooks/m3_models.py"), "interim", "reports/m3_모델링결과", "eda6·m1·m2·r1·w2·m4 다음"),
    Step("s1", (*PY, "notebooks/s1_link_sim.py"), "interim", "reports/s1_LINK-Sim, data/sim (가상 자료)", "실제 자료 비교에 data/interim 필요"),
    Step("t2", (*PY, "notebooks/t2_cross_validation.py"), "none", "reports/t2_교차검증",
         "reports/t1_2축위험도(태연 분석 결과)·r2 CSV 사용"),
    Step("check_no_raw", (*PY, "scripts/check_no_raw.py"), "none", "리포트·analysis CSV 원천 값 열 점검"),
]
KEYS = [s.key for s in STEPS]


def interim_ready() -> bool:
    d = ROOT / "data" / "interim"
    return all((d / f).exists() for f in ("card1.parquet", "card2.parquet", "tel_age.parquet", "tel_time.parquet"))


def raw_ready() -> bool:
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from bigcontest.config import load_config

        cfg = load_config()
        return all((cfg["raw_dir"] / f).exists() for f in cfg["raw_files"].values())
    except Exception:  # noqa: BLE001 - 설정·원본이 없으면 그냥 '없음'
        return False


def plan(args) -> list[tuple[Step, str | None]]:
    """(단계, 건너뛰는 이유 또는 None)."""
    have_interim = interim_ready()
    out = []
    for s in STEPS:
        why = None
        if args.only and s.key not in args.only:
            why = "--only 밖"
        elif s.key == "ingest" and not args.ingest:
            why = "적재는 --ingest 일 때만"
        elif s.key == "w2_labels" and not args.relabel:
            why = "정답 세트는 고정 입력 (--relabel 일 때만)"
        elif args.no_raw and s.needs != "none":
            why = "--no-raw (원본 필요)"
        elif s.needs == "raw" and not raw_ready():
            why = "대회 원본 zip 없음 (BC_RAW_DIR)"
        elif s.needs == "interim" and not have_interim and not (args.ingest and raw_ready()):
            why = "data/interim 없음 (원본 적재 필요)"
        out.append((s, why))
    return out


def norm_bytes(p: Path) -> bytes:
    return p.read_bytes().replace(b"\r\n", b"\n")


def result_hashes(root: Path = ROOT) -> dict[str, str]:
    """reports/**/*.csv → sha256 (줄 끝 맞춤). 키는 reports 기준 상대 경로(슬래시)."""
    rep = root / "reports"
    out = {}
    for p in sorted(rep.rglob("*.csv")):
        if p.name in NOT_HASHED:
            continue
        out[p.relative_to(rep).as_posix()] = hashlib.sha256(norm_bytes(p)).hexdigest()
    return out


def tracked_csvs(root: Path = ROOT) -> set[str] | None:
    """git 추적 및 무시되지 않은 신규 reports/**/*.csv (reports 기준 상대 경로). git 을 못 쓰면 None."""
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "reports"], cwd=root,
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    names = [x.decode("utf-8") for x in out.split(b"\0") if x]
    return {n.removeprefix("reports/") for n in names if n.endswith(".csv") and Path(n).name not in NOT_HASHED}


HEADERS = {
    "verified": ["# 제출·추적 리포트 CSV sha256 (줄 끝 LF 로 맞춘 뒤). scripts/reproduce.py --write-hash 로 만듦",
                 "# 명시적 목록: --check-hash 에서 값이 다르거나, 여기 있는 파일이 없거나, 여기 없는 CSV 가 생기면 실패"],
    "local": ["# 로컬 전용 결과 CSV sha256 (레포에 올리지 않음, .gitignore). scripts/reproduce.py --write-hash 로 만듦",
              "# 파일이 있을 때만 비교 (없으면 건너뜀). 다르면 실패"],
}


def write_hashes(h: dict[str, str], path: Path = HASH_FILE, kind: str = "verified") -> None:
    lines = list(HEADERS[kind])
    lines += [f"{v}  {k}" for k, v in sorted(h.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_hashes(path: Path = HASH_FILE) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.startswith("#") or not ln.strip():
            continue
        v, k = ln.split("  ", 1)
        out[k] = v
    return out


def compare_hashes(now: dict[str, str], ref: dict[str, str]) -> dict[str, list[str]]:
    return {"같음": sorted(k for k in now if ref.get(k) == now[k]),
            "다름": sorted(k for k in now if k in ref and ref[k] != now[k]),
            "새 파일": sorted(k for k in now if k not in ref),
            "없어짐": sorted(k for k in ref if k not in now)}


def check_hashes(root: Path = ROOT, hash_file: Path = HASH_FILE, local_file: Path = LOCAL_HASH_FILE) -> tuple[bool, dict]:
    """검증 목록: 다름·새 파일·없어짐이 하나라도 있으면 실패. 로컬 전용: 있는 파일만 비교, 다르면 실패."""
    ref, ref_local = read_hashes(hash_file), read_hashes(local_file)
    local_names = set(ref_local) | LOCAL_ONLY
    allh = result_hashes(root)
    now = {k: v for k, v in allh.items() if k not in local_names}
    now_local = {k: v for k, v in allh.items() if k in local_names}
    c = compare_hashes(now, ref)
    cl = {"같음": sorted(k for k in ref_local if now_local.get(k) == ref_local[k]),
          "다름": sorted(k for k in ref_local if k in now_local and now_local[k] != ref_local[k]),
          "건너뜀": sorted(k for k in ref_local if k not in now_local)}
    ok = not (c["다름"] or c["새 파일"] or c["없어짐"] or cl["다름"])
    return ok, {"검증": c, "로컬": cl}


def write_all_hashes(root: Path = ROOT, hash_file: Path = HASH_FILE, local_file: Path = LOCAL_HASH_FILE) -> tuple[int, int]:
    """검증 목록 = git 추적 CSV (git 이 없으면 로컬 전용을 뺀 전부). 로컬 전용은 파일이 있으면 새 값, 없으면 기존 값 유지."""
    now = result_hashes(root)
    tracked = tracked_csvs(root)
    names = (tracked if tracked is not None else set(now)) - LOCAL_ONLY
    missing = sorted(names - set(now))
    if missing:
        raise SystemExit(f"추적 CSV 가 디스크에 없음: {missing}")
    write_hashes({k: now[k] for k in names}, hash_file, "verified")
    local = read_hashes(local_file)
    local.update({k: v for k, v in now.items() if k in LOCAL_ONLY})
    write_hashes(local, local_file, "local")
    return len(names), len(local)


def run(args) -> int:
    steps = plan(args)
    print(f"{'단계':<14}{'원본':<9}{'실행':<6}설명")
    for s, why in steps:
        print(f"{s.key:<14}{s.needs:<9}{'예' if why is None else '아니오':<6}{why or s.out}")
    if args.dry_run:
        return 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "MPLBACKEND": "Agg"}
    log, failed = [], []
    for s, why in steps:
        if why:
            log.append({"단계": s.key, "원본 필요": s.needs, "실행": "건너뜀", "사유": why, "초": ""})
            continue
        t = time.perf_counter()
        print(f"\n▶ {s.key}: {' '.join(s.cmd)}", flush=True)
        rc = subprocess.run(s.cmd, cwd=ROOT, env=env).returncode
        sec = time.perf_counter() - t
        log.append({"단계": s.key, "원본 필요": s.needs, "실행": "성공" if rc == 0 else f"실패({rc})", "사유": "", "초": f"{sec:.0f}"})
        print(f"  {s.key} {'성공' if rc == 0 else '실패'} ({sec:.0f}초)", flush=True)
        if rc != 0:
            failed.append(s.key)
            if not args.keep_going:
                break
    with open(LOG_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["단계", "원본 필요", "실행", "사유", "초"])
        w.writeheader()
        w.writerows(log)
    if args.write_hash:
        n, n_local = write_all_hashes()
        print(f"해시 저장: {HASH_FILE.relative_to(ROOT)} ({n}개), {LOCAL_HASH_FILE.relative_to(ROOT)} ({n_local}개)")
    if args.check_hash:
        if not HASH_FILE.exists():
            print("해시 파일 없음: --write-hash 로 먼저 만드세요")
            return 2
        ok, res = check_hashes()
        c, cl = res["검증"], res["로컬"]
        print(f"해시 비교 (검증 목록): 같음 {len(c['같음'])}, 다름 {len(c['다름'])}, 새 파일 {len(c['새 파일'])}, 없어짐 {len(c['없어짐'])}")
        for kind in ("다름", "새 파일", "없어짐"):
            for k in c[kind]:
                print(f"  {kind}: {k}")
        print(f"해시 비교 (로컬 전용): 같음 {len(cl['같음'])}, 다름 {len(cl['다름'])}, 건너뜀(파일 없음) {len(cl['건너뜀'])}")
        for k in cl["다름"]:
            print(f"  로컬 다름: {k}")
        if not ok:
            return 1
    return 1 if failed else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="분석 리포트 재현")
    ap.add_argument("--dry-run", action="store_true", help="순서·원본 필요 여부만 출력")
    ap.add_argument("--no-raw", action="store_true", help="원본 없이 되는 단계만")
    ap.add_argument("--ingest", action="store_true", help="대회 원본 zip 적재부터 (data/interim 을 다시 씀)")
    ap.add_argument("--relabel", action="store_true", help="자원 추천 평가 정답 세트도 다시 만듦")
    ap.add_argument("--only", nargs="+", choices=KEYS, help="이 단계만")
    ap.add_argument("--keep-going", action="store_true", help="실패해도 다음 단계 계속")
    ap.add_argument("--write-hash", action="store_true", help="끝난 뒤 reports/결과_해시.txt (+ _로컬) 를 새로 씀")
    ap.add_argument("--check-hash", action="store_true",
                    help="끝난 뒤 reports/결과_해시.txt 와 비교 (다름·새 파일·없어짐이면 종료 코드 1)")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
