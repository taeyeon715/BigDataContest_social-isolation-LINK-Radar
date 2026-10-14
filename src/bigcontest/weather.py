"""기상청 ASOS 일자료를 받아 저장하고 읽는다.

받기: uv run python -m bigcontest.weather   (.env의 DATA_GO_KR_API_KEY 필요, Decoding 키)
키 없이 다시 만들기: uv run python -m bigcontest.weather --offline   (raw 에 남은 마지막 원문으로)
비교만: uv run python -m bigcontest.weather --offline --check   (지금 CSV와 같은지 보고, 쓰지 않음)

- 원본 응답(JSON)은 external/weather/raw/asos-<실행 시각>/ 에 그대로 보관한다 (git 추적 안 함, .gitignore).
  같은 폴더의 fetch_log.json 에 요청 변수(키는 가림)·받은 시각을 남긴다.
- 실행마다 external/weather/수집_기록.csv 에 한 줄 (git 추적).
- 오류 문장에는 키를 가려서 남긴다 (키가 URL 쿼리 serviceKey= 에 들어가므로).
- 분석용 표는 external/weather/kma_asos_daily_2025-07_12.csv
- 강수량이 비어 있으면 '비 안 옴(0)'으로 본다. 기상청 일자료는 무강수일을 빈 값으로 준다.
  다른 항목의 빈 값은 결측으로 둔다.
- 새로 받은 값이 지금 CSV와 다르면 CSV를 바꾸지 않고 차이만 알린다 (시연 결과가 몰래 바뀌지 않게).
  바꾸려면 --write 를 붙인다.
"""

import argparse
import csv
import io
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.resources.collect import mask_secrets

URL = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
STATIONS = {"강남": 108, "춘천": 101}  # 서울(종로구 송월동) 관측소가 강남 대표값
START, END = "20250701", "20251231"
OUT = ROOT / "external" / "weather"
CSV = OUT / "kma_asos_daily_2025-07_12.csv"
RAW = OUT / "raw"
RUN_LOG = OUT / "수집_기록.csv"
LOG_COLS = ["run_id", "started_at", "stations", "start", "end", "status", "rows", "same_as_csv", "raw_dir", "message"]
FIELDS = {
    "avgTa": "tavg",
    "minTa": "tmin",
    "maxTa": "tmax",
    "sumRn": "rain",
    "ddMes": "snow_new",
    "avgRhm": "humidity",
    "avgWs": "wind",
}


def _key() -> str:
    key = os.environ.get("DATA_GO_KR_API_KEY")
    if not key and (ROOT / ".env").exists():
        text = (ROOT / ".env").read_text(encoding="utf-8")
        m = re.search(r"^DATA_GO_KR_API_KEY=(.*)$", text, re.M)
        key = m.group(1).strip() if m else ""
    if not key:
        raise SystemExit(".env에 DATA_GO_KR_API_KEY가 없습니다")
    return key


def _params(stn: int) -> dict:
    return {"pageNo": 1, "numOfRows": 999, "dataType": "JSON", "dataCd": "ASOS", "dateCd": "DAY",
            "startDt": START, "endDt": END, "stnIds": stn}


def raw_name(stn: int) -> str:
    return f"asos_{stn}_{START}_{END}.json"


def fetch_raw(now: datetime | None = None) -> Path:
    """API를 불러 원문을 raw/asos-<시각>/ 에 저장하고 그 폴더를 돌려준다."""
    key = _key()
    now = now or datetime.now()
    run_dir = RAW / f"asos-{now:%Y%m%dT%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for city, stn in STATIONS.items():
        q = urllib.parse.urlencode({"serviceKey": key, **_params(stn)})
        try:
            with urllib.request.urlopen(f"{URL}?{q}", timeout=60) as resp:
                body = resp.read()
        except OSError as e:  # URLError·HTTPError 문장에 URL(키 포함)이 들어갈 수 있음
            raise SystemExit(mask_secrets(f"{city}({stn}) 요청 실패: {type(e).__name__}: {e}")) from None
        header = json.loads(body)["response"]["header"]
        if header["resultCode"] != "00":
            raise SystemExit(mask_secrets(f"{city}({stn}) 실패: {header['resultMsg']}"))
        (run_dir / raw_name(stn)).write_bytes(body)
    log = {"fetched_at": now.isoformat(timespec="seconds"), "url": URL,
           "params": {str(s): {"serviceKey": "***", **_params(s)} for s in STATIONS.values()}}
    (run_dir / "fetch_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return run_dir


def latest_raw() -> Path | None:
    runs = sorted(p for p in RAW.glob("asos-*") if all((p / raw_name(s)).exists() for s in STATIONS.values()))
    return runs[-1] if runs else None


def parse_raw(run_dir: Path) -> pd.DataFrame:
    """원문 폴더 → 분석용 표 (CSV와 같은 칸·순서)."""
    frames = []
    for city, stn in STATIONS.items():
        data = json.loads((Path(run_dir) / raw_name(stn)).read_text(encoding="utf-8"))
        df = pd.DataFrame(data["response"]["body"]["items"]["item"])
        df = df[["tm", "stnId"] + list(FIELDS)].rename(columns={"tm": "date", "stnId": "stn", **FIELDS})
        for c in FIELDS.values():
            df[c] = pd.to_numeric(df[c].replace("", None))
        df["rain"] = df["rain"].fillna(0.0)
        df.insert(0, "city", city)
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def same_as_csv(df: pd.DataFrame, path: Path = CSV) -> bool:
    """CSV로 썼다가 다시 읽었을 때 지금 파일과 값이 같은지."""
    if not Path(path).exists():
        return False
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    buf.seek(0)
    return pd.read_csv(buf).equals(pd.read_csv(path))


def _log(row: dict):
    new = not RUN_LOG.exists()
    with open(RUN_LOG, "a", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def fetch(write: bool = False) -> pd.DataFrame:
    """받기 → 원문 보존 → 표. 지금 CSV와 다르면 write=True 일 때만 CSV를 바꾼다."""
    now = datetime.now()
    run_dir = fetch_raw(now)
    out = parse_raw(run_dir)
    same = same_as_csv(out)
    if write or not CSV.exists():
        out.to_csv(CSV, index=False, encoding="utf-8")
    _log({"run_id": run_dir.name, "started_at": now.isoformat(timespec="seconds"),
          "stations": " ".join(f"{c}={s}" for c, s in STATIONS.items()), "start": START, "end": END,
          "status": "성공", "rows": len(out), "same_as_csv": "예" if same else "아니오",
          "raw_dir": run_dir.relative_to(ROOT).as_posix(), "message": "" if same or write else "CSV 유지 (차이 있음)"})
    return out


def load() -> pd.DataFrame:
    d = pd.read_csv(CSV, parse_dates=["date"])
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description="기상청 ASOS 일자료")
    ap.add_argument("--offline", action="store_true", help="API 를 부르지 않고 raw 의 마지막 원문으로 다시 만듦")
    ap.add_argument("--check", action="store_true", help="지금 CSV와 같은지 보기만 하고 쓰지 않음")
    ap.add_argument("--write", action="store_true", help="값이 달라도 CSV를 새 값으로 바꿈")
    a = ap.parse_args(argv)
    if a.offline:
        run_dir = latest_raw()
        if run_dir is None:
            raise SystemExit("raw 원문이 없습니다 (먼저 키로 받기)")
        d = parse_raw(run_dir)
        same = same_as_csv(d)
        print(f"{run_dir.relative_to(ROOT).as_posix()} → {len(d)}행, 지금 CSV와 같음: {same}")
        if not a.check and (same or a.write):
            d.to_csv(CSV, index=False, encoding="utf-8")
    else:
        d = fetch(write=a.write)
    print(d.groupby("city").agg(days=("date", "count"), first=("date", "min"), last=("date", "max"),
                                rain_days=("rain", lambda s: int((s > 0).sum())), tmax=("tmax", "max")))


if __name__ == "__main__":
    main()
