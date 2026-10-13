"""기상청 ASOS 일자료를 받아 저장하고 읽는다.

받기: uv run python -m bigcontest.weather   (.env의 DATA_GO_KR_API_KEY 필요, Decoding 키)

- 원본 응답(JSON)은 external/weather/raw/ 에 보관한다 (git 추적 안 함, .gitignore).
- 오류 문장에는 키를 가려서 남긴다 (키가 URL 쿼리 serviceKey= 에 들어가므로).
- 분석용 표는 external/weather/kma_asos_daily_2025-07_12.csv
- 강수량이 비어 있으면 '비 안 옴(0)'으로 본다. 기상청 일자료는 무강수일을 빈 값으로 준다.
  다른 항목의 빈 값은 결측으로 둔다.
"""

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

from bigcontest.config import ROOT
from bigcontest.resources.collect import mask_secrets

URL = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
STATIONS = {"강남": 108, "춘천": 101}  # 서울(종로구 송월동) 관측소가 강남 대표값
START, END = "20250701", "20251231"
OUT = ROOT / "external" / "weather"
CSV = OUT / "kma_asos_daily_2025-07_12.csv"
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
    if not key:
        text = (ROOT / ".env").read_text(encoding="utf-8")
        m = re.search(r"^DATA_GO_KR_API_KEY=(.*)$", text, re.M)
        key = m.group(1).strip() if m else ""
    if not key:
        raise SystemExit(".env에 DATA_GO_KR_API_KEY가 없습니다")
    return key


def fetch() -> pd.DataFrame:
    key = _key()
    raw_dir = OUT / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for city, stn in STATIONS.items():
        q = urllib.parse.urlencode({
            "serviceKey": key, "pageNo": 1, "numOfRows": 999, "dataType": "JSON",
            "dataCd": "ASOS", "dateCd": "DAY", "startDt": START, "endDt": END, "stnIds": stn,
        })
        try:
            with urllib.request.urlopen(f"{URL}?{q}", timeout=60) as resp:
                body = resp.read().decode("utf-8")
        except OSError as e:  # URLError·HTTPError 문장에 URL(키 포함)이 들어갈 수 있음
            raise SystemExit(mask_secrets(f"{city}({stn}) 요청 실패: {type(e).__name__}: {e}")) from None
        data = json.loads(body)
        header = data["response"]["header"]
        if header["resultCode"] != "00":
            raise SystemExit(mask_secrets(f"{city}({stn}) 실패: {header['resultMsg']}"))
        (raw_dir / f"asos_{stn}_{START}_{END}.json").write_text(body, encoding="utf-8")
        items = data["response"]["body"]["items"]["item"]
        df = pd.DataFrame(items)
        df = df[["tm", "stnId"] + list(FIELDS)].rename(columns={"tm": "date", "stnId": "stn", **FIELDS})
        for c in FIELDS.values():
            df[c] = pd.to_numeric(df[c].replace("", None))
        df["rain"] = df["rain"].fillna(0.0)
        df.insert(0, "city", city)
        frames.append(df)
    out = pd.concat(frames, ignore_index=True)
    out.to_csv(CSV, index=False, encoding="utf-8")
    log = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "rows": {c: int((out["city"] == c).sum()) for c in STATIONS}}
    (raw_dir / "fetch_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load() -> pd.DataFrame:
    d = pd.read_csv(CSV, parse_dates=["date"])
    return d


if __name__ == "__main__":
    d = fetch()
    print(d.groupby("city").agg(days=("date", "count"), first=("date", "min"), last=("date", "max"),
                                rain_days=("rain", lambda s: int((s > 0).sum())), tmax=("tmax", "max")))
