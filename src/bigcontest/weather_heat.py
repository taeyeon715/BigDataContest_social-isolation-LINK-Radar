"""ASOS 일자료로 일 최고 체감온도(근사)를 만든다 (R4 폭염 특보 단계용).

받기: uv run python -m bigcontest.weather_heat            (.env 의 DATA_GO_KR_API_KEY, Decoding 키)
키 없이 다시 만들기: uv run python -m bigcontest.weather_heat --offline   (raw 에 남은 마지막 원문으로)

- 같은 API(기상청 ASOS 일자료 조회서비스)에서 일 최고기온(maxTa)·최고기온 시각(maxTaHrmt)·일 최소 상대습도(minRhm)·
  최소 습도 시각(minRhmHrmt)·일 평균 상대습도(avgRhm)를 받는다. 기존 kma_asos_daily_2025-07_12.csv 는 바꾸지 않는다.
- 원문 JSON 은 external/weather/raw/heat-<시각>/ (git 추적 안 함), 실행마다 external/weather/수집_기록_체감온도.csv 한 줄.
- 결과: external/weather/kma_asos_heat_2025-07_12.csv
  칸: city, date, stn, tmax, tmax_hhmm, rh_min, rh_min_hhmm, rh_avg, at_max (tmax·rh_min), at_max_hi (tmax·rh_avg)
- 식과 근사 이유는 bigcontest.rules.heat.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

from bigcontest.resources.collect import mask_secrets
from bigcontest.rules.heat import apparent_temp
from bigcontest.weather import END, OUT, RAW, START, STATIONS, URL, _key, _params, raw_name

CSV = OUT / "kma_asos_heat_2025-07_12.csv"
RUN_LOG = OUT / "수집_기록_체감온도.csv"
LOG_COLS = ["run_id", "started_at", "stations", "start", "end", "status", "rows", "raw_dir", "message"]
FIELDS = {"maxTa": "tmax", "maxTaHrmt": "tmax_hhmm", "minRhm": "rh_min", "minRhmHrmt": "rh_min_hhmm", "avgRhm": "rh_avg"}


def fetch_raw(now: datetime | None = None) -> Path:
    key = _key()
    now = now or datetime.now()
    run_dir = RAW / f"heat-{now:%Y%m%dT%H%M%S}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for city, stn in STATIONS.items():
        q = urllib.parse.urlencode({"serviceKey": key, **_params(stn)})
        try:
            with urllib.request.urlopen(f"{URL}?{q}", timeout=60) as resp:
                body = resp.read()
        except OSError as e:
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
    runs = sorted(p for p in RAW.glob("heat-*") if all((p / raw_name(s)).exists() for s in STATIONS.values()))
    return runs[-1] if runs else None


def parse_raw(run_dir: Path) -> pd.DataFrame:
    frames = []
    for city, stn in STATIONS.items():
        data = json.loads((Path(run_dir) / raw_name(stn)).read_text(encoding="utf-8"))
        df = pd.DataFrame(data["response"]["body"]["items"]["item"])
        df = df[["tm", "stnId"] + list(FIELDS)].rename(columns={"tm": "date", "stnId": "stn", **FIELDS})
        for c in ("tmax", "rh_min", "rh_avg"):
            df[c] = pd.to_numeric(df[c].replace("", None))
        df.insert(0, "city", city)
        frames.append(df)
    d = pd.concat(frames, ignore_index=True)
    d["at_max"] = [None if v is None else round(v, 1) for v in map(apparent_temp, d["tmax"], d["rh_min"])]
    d["at_max_hi"] = [None if v is None else round(v, 1) for v in map(apparent_temp, d["tmax"], d["rh_avg"])]
    return d


def _log(row: dict) -> None:
    new = not RUN_LOG.exists()
    with open(RUN_LOG, "a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="API 를 부르지 않고 raw 의 마지막 원문으로")
    a = ap.parse_args(argv)
    now = datetime.now()
    run_dir = latest_raw() if a.offline else fetch_raw(now)
    if run_dir is None:
        raise SystemExit("raw 원문 없음 (--offline 은 먼저 한 번 받아야 함)")
    d = parse_raw(run_dir)
    d.to_csv(CSV, index=False, encoding="utf-8")
    if not a.offline:
        _log({"run_id": run_dir.name, "started_at": now.isoformat(timespec="seconds"),
              "stations": " ".join(f"{c}={s}" for c, s in STATIONS.items()), "start": START, "end": END,
              "status": "성공", "rows": len(d), "raw_dir": run_dir.relative_to(OUT.parent.parent).as_posix(), "message": ""})
    print(f"{CSV.name}: {len(d)}행 ({run_dir.name})")


if __name__ == "__main__":
    main()
