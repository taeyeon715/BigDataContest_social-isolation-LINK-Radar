"""원본 zip을 읽어 정리된 Parquet로 저장한다.

실행: uv run python -m bigcontest.ingest

정리 규칙
- 통신: 완전 중복 행 제거(12월 시간대·요일 파일), 강남·춘천 외 칸 제외, 행정동 코드 추가
- 카드: cp949로 읽기, 날짜·연령 정리, 업종 묶음·본사등록·법인·공휴일 표시
  (행은 지우지 않고 표시만 한다. 제외는 분석할 때 한다.)

적재 계약
- 원본에 알려진 완전 중복(12월 시간대·요일 파일)은 정리 규칙으로 지운다 (drop_full_duplicates)
- check_new_months·MonthStore 는 월 단위로 더할 때 쓰는 규칙이다(이미 적재한 월·중간 월 누락 거절).
  지금 main() 은 고정 기간(2025-07~12) 전체를 data/interim 에 다시 쓴다. 정정 자료를 새 스냅샷으로 만드는 경로는 아직 연결 전.
- MonthStore: 월 단위로 하나씩 추가하는 저장소. 새 달을 추가해도 이전 달 파일은 그대로다
"""

import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pandas as pd

from bigcontest.categories import EXCLUDED, categorize
from bigcontest.config import ROOT, load_config

TELECOM_KINDS = ["age", "time", "wkdy"]
MONTHS = ["202507", "202508", "202509", "202510", "202511", "202512"]
CLEANING_RULE_VERSION = "clean-v1"


class IngestRuleError(ValueError):
    """적재 계약 위반. code: DUPLICATE_MONTH / MONTH_ALREADY_INGESTED / MONTH_GAP / BAD_MONTH"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def _next_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y + m // 12}{m % 12 + 1:02d}"


def check_new_months(existing: list[str], incoming: list[str]) -> list[str]:
    """이미 적재한 월 뒤에 새 월을 붙여도 되는지 검사하고, 합친 월 목록을 돌려준다.

    - 같은 월이 두 번 들어오면 거절 (incoming 안 중복, 이미 적재한 월)
    - 중간 월이 빠지면 거절 (기존 마지막 월 다음 달부터 빈틈 없이)
    """
    for ym in incoming:
        if len(ym) != 6 or not ym.isdigit() or not 1 <= int(ym[4:]) <= 12:
            raise IngestRuleError("BAD_MONTH", f"월 형식이 YYYYMM 이 아님: {ym}")
    dup = sorted({ym for ym in incoming if incoming.count(ym) > 1})
    if dup:
        raise IngestRuleError("DUPLICATE_MONTH", f"같은 월이 두 번 들어옴: {dup}")
    again = sorted(set(incoming) & set(existing))
    if again:
        raise IngestRuleError("MONTH_ALREADY_INGESTED", f"이미 적재한 월: {again}. 정정 자료는 새 스냅샷으로")
    months = sorted(existing)
    for ym in sorted(incoming):
        if months and ym != _next_month(months[-1]):
            raise IngestRuleError("MONTH_GAP", f"{months[-1]} 다음은 {_next_month(months[-1])} 이어야 하는데 {ym}")
        months.append(ym)
    return months


def drop_full_duplicates(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """정리 규칙: 모든 칸이 같은 행은 하나만 남긴다 (12월 통신 시간대·요일 파일, 주최측 공지)."""
    out = df.drop_duplicates()
    return out, len(df) - len(out)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MonthStore:
    """월 단위 Parquet 저장소: <root>/<source>/<YYYYMM>.parquet + _months.json (적재 기록).

    새 월만 추가할 수 있고 이미 있는 월 파일은 다시 쓰지 않는다.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _ledger(self, source: str) -> Path:
        return self.root / source / "_months.json"

    def months(self, source: str) -> dict[str, dict]:
        p = self._ledger(source)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def append(self, source: str, frames: dict[str, pd.DataFrame]) -> list[str]:
        """새 월들을 추가. 규칙 위반이면 아무것도 쓰지 않고 IngestRuleError."""
        done = self.months(source)
        check_new_months(list(done), list(frames))
        folder = self.root / source
        folder.mkdir(parents=True, exist_ok=True)
        for ym in sorted(frames):
            df, n_dup = drop_full_duplicates(frames[ym])
            path = folder / f"{ym}.parquet"
            tmp = folder / f".{ym}.parquet.tmp"
            df.to_parquet(tmp, index=False)
            os.replace(tmp, path)
            done[ym] = {
                "rows": len(df),
                "dropped_full_duplicates": n_dup,
                "sha256": _sha256(path),
                "cleaning_rule_version": CLEANING_RULE_VERSION,
            }
        tmp = self._ledger(source).with_suffix(".tmp")
        tmp.write_text(json.dumps(done, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self._ledger(source))
        return sorted(done)

    def read(self, source: str, months: list[str] | None = None) -> pd.DataFrame:
        want = sorted(months or self.months(source))
        return pd.concat([pd.read_parquet(self.root / source / f"{ym}.parquet") for ym in want], ignore_index=True)


def _zip_name(info: zipfile.ZipInfo) -> str:
    # zip 안의 한글 파일명이 cp437로 깨져 있어 cp949로 되돌린다
    try:
        return info.filename.encode("cp437").decode("cp949")
    except UnicodeError:
        return info.filename


def _adm_type(city: str, adm_cd: str) -> str:
    # 춘천 행정동 코드 6~8번째 자리: 300 미만 읍, 300번대 면, 500 이상 동
    if city != "춘천":
        return "동"
    n = int(adm_cd[5:8])
    return "읍" if n < 300 else ("면" if n < 500 else "동")


def load_telecom(cfg: dict, log: dict) -> None:
    path = cfg["raw_dir"] / cfg["raw_files"]["telecom_zip"]
    sgg = cfg["sgg"]
    with zipfile.ZipFile(path) as zf:
        for kind in TELECOM_KINDS:
            frames = []
            for ym in MONTHS:
                name = f"flow_{kind}_pop_{ym}.csv"
                df = pd.read_csv(
                    io.BytesIO(zf.read(name)),
                    sep="|",
                    encoding="utf-8-sig",
                    dtype={"STD_YM": str, "BLOCK_CD": str},
                )
                n_raw = len(df)
                df, _ = drop_full_duplicates(df)
                n_dedup = len(df)
                df = df[df["BLOCK_CD"].str[:5].isin(sgg)]
                log["telecom"].append(
                    {"file": name, "rows_raw": n_raw, "rows_dedup": n_dedup, "rows_kept": len(df)}
                )
                frames.append(df)
            out = pd.concat(frames, ignore_index=True)
            out["city"] = out["BLOCK_CD"].str[:5].map(sgg)
            out["adm_cd"] = out["BLOCK_CD"].str[:8]
            out["oa13"] = out["BLOCK_CD"].str[:13]
            out["adm_type"] = [_adm_type(c, a) for c, a in zip(out["city"], out["adm_cd"])]
            out.to_parquet(cfg["interim_dir"] / f"tel_{kind}.parquet", index=False)


def load_card(cfg: dict, log: dict) -> None:
    path = cfg["raw_dir"] / cfg["raw_files"]["card_zip"]
    holidays = cfg["holidays"]
    city_map = {"서울 강남구": "강남", "강원 춘천시": "춘천"}
    home_sido = {"강남": "서울", "춘천": "강원"}
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            name = _zip_name(info)
            if not name.endswith(".txt"):
                continue
            # 데이터1 = 주제1용(시간대 있음, 보조), 데이터2 = 주제2용(거주지 있음, 주력)
            tag = "card1" if name.endswith("1.txt") else "card2"
            df = pd.read_csv(io.BytesIO(zf.read(info)), sep="\t", encoding="cp949", dtype=str)
            df["TS_AT"] = pd.to_numeric(df["TS_AT"])
            df["USE_CNT"] = pd.to_numeric(df["USE_CNT"])
            n_raw = len(df)
            df, _ = drop_full_duplicates(df)
            df["date"] = pd.to_datetime(df["TA_YMD"], format="%Y%m%d")
            df["city"] = df["MCT_SGG_CD"].map(city_map)
            df["age"] = df["AGE_CCD"].str.replace(" ", "", regex=False)
            df["corp"] = df["SEX_CCD"].eq("법인")
            df["cat"] = df["MCT_RY_CD"].map(categorize)
            df["hq"] = df["cat"].eq(EXCLUDED)
            df["dow"] = df["date"].dt.dayofweek
            df["holiday"] = df["date"].dt.strftime("%Y-%m-%d").map(holidays)
            if tag == "card2":
                df["local"] = df["CLN_SGG_CD"].eq(df["city"].map(home_sido))
            log["card"].append(
                {
                    "file": name,
                    "tag": tag,
                    "rows_raw": n_raw,
                    "rows_dedup": len(df),
                    "corp_rows": int(df["corp"].sum()),
                    "hq_rows": int(df["hq"].sum()),
                }
            )
            df.to_parquet(cfg["interim_dir"] / f"{tag}.parquet", index=False)


def write_report(log: dict) -> None:
    lines = ["# EDA-0 적재 결과", "", "[← 전체 리포트 목록](../README.md)", "", "`uv run python -m bigcontest.ingest` 실행 결과입니다.", ""]
    lines += ["## 통신", "", "| 파일 | 원본 행 | 중복 제거 후 | 강남·춘천만 |", "|---|---:|---:|---:|"]
    for r in log["telecom"]:
        lines.append(f"| {r['file']} | {r['rows_raw']:,} | {r['rows_dedup']:,} | {r['rows_kept']:,} |")
    lines += ["", "## 카드", "", "| 파일 | 구분 | 원본 행 | 중복 제거 후 | 법인 행 | 본사등록 업종 행 |", "|---|---|---:|---:|---:|---:|"]
    for r in log["card"]:
        lines.append(
            f"| {r['file']} | {r['tag']} | {r['rows_raw']:,} | {r['rows_dedup']:,} | {r['corp_rows']:,} | {r['hq_rows']:,} |"
        )
    out = ROOT / "reports" / "eda0_적재결과"
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    from bigcontest.gallery import build

    build()


def main() -> None:
    check_new_months([], MONTHS)  # 적재 월 목록 자체가 중복·빈틈 없는지
    cfg = load_config()
    cfg["interim_dir"].mkdir(parents=True, exist_ok=True)
    log = {"telecom": [], "card": []}
    load_telecom(cfg, log)
    load_card(cfg, log)
    (cfg["interim_dir"] / "ingest_log.json").write_text(
        json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_report(log)
    print("done")


if __name__ == "__main__":
    main()
