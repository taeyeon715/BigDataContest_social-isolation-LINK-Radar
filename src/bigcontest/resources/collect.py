"""자원 수집 틀. 실제 출처(E-Gen·복지로)는 resources/sources.py 가 이 틀로 받는다.

규칙
- 수집 실행마다 기록: 실행 ID, 시각, 호출 수, 성공·실패 수 → 수집_기록.csv
- API 원문은 그대로 external/resources/raw/<출처>/<실행 ID>/ 에 보관 (원장은 원문에서 다시 만들 수 있게)
- 호출 한도·재시도 횟수는 configs/resource_collect.yaml
- 수집이 끝까지 성공하지 못하면 원장을 바꾸지 않음 (이전 값 유지, last_verified 그대로)
- 새 목록에서 사라진 자원은 "종료"가 아니라 operating_status="확인 필요"
- 손으로 고친 값은 정정_기록.csv에 새 줄로 남김 (덮어쓰지 않음). 수집해도 정정한 칸은 바꾸지 않음

Fetcher를 상속해 fetch(page)와 parse(raw)만 만들면 된다. 테스트는 가짜 Fetcher로 한다.
- 오류 문장은 기록 전에 키를 가린다 (`mask_secrets`, serviceKey=…·confmKey=… → ***). 키가 URL 쿼리에 들어가기 때문.
"""

import csv
import os
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from bigcontest.config import ROOT

CONFIG = ROOT / "configs" / "resource_collect.yaml"
LOG_COLS = [
    "run_id", "source", "started_at", "finished_at", "calls", "success", "fail", "status",
    "n_records", "n_updated", "n_vanished", "n_new", "raw_dir", "message",
]
CORR_COLS = ["correction_id", "resource_id", "field", "old_value", "new_value", "reason", "corrected_by", "corrected_at"]


def load_collect_config(path: Path = CONFIG) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for k in ["raw_dir", "run_log", "corrections"]:
        cfg[k] = ROOT / cfg[k]
    return cfg


# ------------------------------------------------------------ 정정 기록
def load_corrections(path: Path | None = None) -> pd.DataFrame:
    path = path or load_collect_config()["corrections"]
    if not Path(path).exists():
        return pd.DataFrame(columns=CORR_COLS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def add_correction(path: Path, resource_id: str, field_: str, old: str, new: str, reason: str,
                   by: str, at: str | None = None) -> dict:
    """정정 한 건을 파일 끝에 덧붙인다. 기존 줄은 절대 고치지 않는다."""
    if not reason or not by:
        raise ValueError("정정에는 이유와 고친 사람이 있어야 합니다")
    path = Path(path)
    prev = load_corrections(path)
    row = {
        "correction_id": f"C{len(prev) + 1:04d}",
        "resource_id": resource_id, "field": field_, "old_value": old, "new_value": new,
        "reason": reason, "corrected_by": by, "corrected_at": at or datetime.now().strftime("%Y-%m-%d"),
    }
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CORR_COLS)
        if new_file:
            w.writeheader()
        w.writerow(row)
    return row


def apply_corrections(df: pd.DataFrame, corr: pd.DataFrame, id_col: str = "resource_id") -> pd.DataFrame:
    """정정 기록을 위에서부터 차례로 적용 (같은 칸을 여러 번 고쳤으면 마지막 값)."""
    df = df.copy()
    pos = {rid: i for i, rid in enumerate(df[id_col])}
    for _, c in corr.iterrows():
        if c["resource_id"] not in pos:
            raise ValueError(f"정정 기록의 자원이 원장에 없음: {c['resource_id']}")
        if c["field"] not in df.columns:
            raise ValueError(f"정정 기록의 칸이 원장에 없음: {c['field']}")
        df.iloc[pos[c["resource_id"]], df.columns.get_loc(c["field"])] = c["new_value"]
    return df


def protected_cells(corr: pd.DataFrame) -> set[tuple[str, str]]:
    """손으로 고친 (자원, 칸). 수집이 덮어쓰지 않는다."""
    return set(zip(corr["resource_id"], corr["field"]))


# ------------------------------------------------------------ 수집
@dataclass
class RawPage:
    body: bytes          # API 응답 원문 그대로
    ext: str = "json"    # 저장 확장자 (json / xml)
    last: bool = True    # 마지막 페이지인지


class Fetcher(ABC):
    """출처 하나의 목록을 받는 방법. source는 원장의 src 값과 같아야 한다."""

    source: str = ""

    @abstractmethod
    def fetch(self, page: int) -> RawPage:
        """page번째 응답 원문. 실패하면 예외."""

    @abstractmethod
    def parse(self, raw: RawPage) -> list[dict]:
        """원문 → 기록 목록. 기록마다 source_record_id가 있어야 한다."""


@dataclass
class CollectResult:
    ledger: pd.DataFrame
    run: dict
    new_records: pd.DataFrame = field(default_factory=pd.DataFrame)  # 원장에 없는 기록 (사람이 검토 후 추가)
    vanished: list[str] = field(default_factory=list)                 # 새 목록에서 빠진 resource_id


_SECRET_RE = re.compile(r"(?i)\b(serviceKey|confmKey|authKey|api_?key|key)=([^&\s\"'<>]+)")


def mask_secrets(text: str) -> str:
    """URL·오류 문장 속 API 키 값을 *** 로 가린다 (원문 응답·예외 문장을 기록하기 전에)."""
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", str(text))


def env_key(name: str = "DATA_GO_KR_API_KEY") -> str:
    """API 키 (환경 변수, 없으면 .env). 값은 어디에도 기록하지 않는다. 없으면 빈 값."""
    key = os.environ.get(name, "")
    env = ROOT / ".env"
    if not key and env.exists():
        m = re.search(rf"^{re.escape(name)}=(.*)$", env.read_text(encoding="utf-8"), re.M)
        key = m.group(1).strip() if m else ""
    return key


def _append_log(path: Path, row: dict):
    path = Path(path)
    new_file = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def run_collection(ledger: pd.DataFrame, fetcher: Fetcher, fields: list[str], cfg: dict | None = None,
                   now: datetime | None = None, corrections: pd.DataFrame | None = None) -> CollectResult:
    """출처 하나를 수집해 원장을 갱신한다.

    fields: 수집 기록에서 원장으로 옮길 칸 (예: ["name", "address", "phone"])
    원장의 src == fetcher.source 인 행만 이 수집의 대상이다. 짝은 source_record_id로 맞춘다.
    """
    cfg = cfg or load_collect_config()
    now = now or datetime.now()
    corrections = load_corrections(cfg["corrections"]) if corrections is None else corrections
    run_id = f"{fetcher.source}-{now:%Y%m%dT%H%M%S}"
    raw_dir = Path(cfg["raw_dir"]) / fetcher.source / run_id
    calls = success = fail = 0
    status, message, records = "", "", []

    page = 1
    while not status:
        raw, budget_hit = None, False
        for _ in range(1 + cfg["max_retries"]):
            if calls >= cfg["max_calls"]:
                budget_hit = True
                break
            calls += 1
            try:
                raw = fetcher.fetch(page)
                success += 1
                break
            except Exception as e:  # 원인은 기록만 하고 재시도
                fail += 1
                message = mask_secrets(f"page {page}: {type(e).__name__}: {e}")
                if cfg.get("retry_wait_sec"):
                    time.sleep(cfg["retry_wait_sec"])
        if raw is None:
            status = "한도 초과" if budget_hit else "실패"
            break
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"page{page:03d}.{raw.ext}").write_bytes(raw.body)
        try:
            records += fetcher.parse(raw)
        except Exception as e:
            status, message = "실패", mask_secrets(f"page {page} 해석 실패: {type(e).__name__}: {e}")
            break
        if raw.last:
            status = "성공"
        page += 1

    run = {
        "run_id": run_id, "source": fetcher.source, "started_at": now.isoformat(timespec="seconds"),
        "finished_at": datetime.now().isoformat(timespec="seconds"), "calls": calls, "success": success,
        "fail": fail, "status": status, "n_records": len(records), "n_updated": 0, "n_vanished": 0, "n_new": 0,
        "raw_dir": raw_dir.relative_to(ROOT).as_posix() if raw_dir.is_relative_to(ROOT) else str(raw_dir),
        "message": message,
    }
    if status != "성공":
        # 끝까지 받지 못한 목록으로는 "사라진 자원"을 판단할 수 없으므로 원장을 그대로 둔다
        _append_log(cfg["run_log"], run)
        return CollectResult(ledger=ledger.copy(), run=run)

    out = ledger.copy()
    got = {str(r["source_record_id"]): r for r in records}
    locked = protected_cells(corrections)
    today = now.strftime("%Y-%m-%d")
    scope = out.index[(out["src"] == fetcher.source) & (out["source_record_id"] != "")]
    vanished = []
    for i in scope:
        rid, sid = out.at[i, "resource_id"], out.at[i, "source_record_id"]
        if sid not in got:
            out.at[i, "operating_status"] = "확인 필요"  # 종료로 단정하지 않음
            vanished.append(rid)
            continue
        rec = got[sid]
        for f in fields:
            if f in rec and (rid, f) not in locked:
                out.at[i, f] = rec[f]
        if out.at[i, "operating_status"] == "확인 필요" and (rid, "operating_status") not in locked:
            out.at[i, "operating_status"] = rec.get("operating_status", "미상")
        out.at[i, "last_verified"] = today
    known = set(out.loc[scope, "source_record_id"])
    new = pd.DataFrame([r for s, r in got.items() if s not in known])
    run.update(n_updated=len(scope) - len(vanished), n_vanished=len(vanished), n_new=len(new))
    _append_log(cfg["run_log"], run)
    return CollectResult(ledger=out, run=run, new_records=new, vanished=vanished)
