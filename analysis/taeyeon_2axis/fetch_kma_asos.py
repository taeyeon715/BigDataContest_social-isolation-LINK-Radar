"""
기상청 지상(종관, ASOS) 일자료 조회서비스 → external/weather/kma_asos_daily_서울_춘천_202507_202512.csv

- 인증키는 이 폴더의 .env 파일(KMA_API_KEY=...) 또는 환경변수 KMA_API_KEY 에서 읽습니다.
- 공공데이터포털 '일반 인증키'(Decoding 키)를 넣으면 됩니다. Encoding 키를 넣어도 자동으로 처리합니다.
- 활용가이드: 기상청02_지상(종관,ASOS)일자료_조회서비스_오픈API활용가이드.docx

실행: python fetch_kma_asos.py
"""
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "external" / "weather" / "kma_asos_daily_서울_춘천_202507_202512.csv"
URL = "https://apis.data.go.kr/1360000/AsosDalyInfoService/getWthrDataList"
STATIONS = {"108": "서울", "101": "춘천"}
START, END = "20250701", "20251231"
COLS = {"stnId": "지점", "stnNm": "지점명", "tm": "일자", "avgTa": "평균기온", "minTa": "최저기온", "maxTa": "최고기온",
        "sumRn": "일강수량", "hr1MaxRn": "1시간최다강수량", "sumRnDur": "강수계속시간", "avgWs": "평균풍속",
        "avgRhm": "평균상대습도", "ddMefs": "일최심신적설", "sumSsHr": "합계일조시간", "iscs": "일기현상"}


def load_key():
    key = os.environ.get("KMA_API_KEY")
    env = next((p for p in [BASE / ".env", BASE.parent / ".env"] if p.exists()), BASE / ".env")   # 패키지 폴더 또는 상위 폴더
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("KMA_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit(".env 파일에 KMA_API_KEY=발급받은키 를 넣어 주세요.")
    return urllib.parse.unquote(key)       # Encoding 키가 들어와도 Decoding 형태로 통일


def fetch(key, stn):
    q = {"serviceKey": key, "numOfRows": 400, "pageNo": 1, "dataType": "JSON", "dataCd": "ASOS",
         "dateCd": "DAY", "startDt": START, "endDt": END, "stnIds": stn}
    with urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(q), timeout=60) as r:
        body = r.read().decode("utf-8")
    try:
        res = json.loads(body)["response"]
    except (json.JSONDecodeError, KeyError):
        sys.exit(f"[{stn}] 응답 오류 (키 등록 직후라면 최대 1시간 뒤 다시 시도):\n{body[:300]}")
    head = res["header"]
    if head["resultCode"] != "00":
        sys.exit(f"[{stn}] {head['resultCode']} {head['resultMsg']}")
    return pd.DataFrame(res["body"]["items"]["item"])


def main():
    key = load_key()
    frames = []
    for stn, name in STATIONS.items():
        d = fetch(key, stn)
        print(f"{name}({stn}): {len(d)}일")
        frames.append(d)
    df = pd.concat(frames)[list(COLS)].rename(columns=COLS)
    for c in ["평균기온", "최저기온", "최고기온", "일강수량", "1시간최다강수량", "강수계속시간", "평균풍속",
              "평균상대습도", "일최심신적설", "합계일조시간"]:
        df[c] = pd.to_numeric(df[c].replace("", None), errors="coerce")
    df["지역"] = df["지점"].map({"108": "서울 강남구", "101": "강원 춘천시"})
    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT, index=False, encoding="utf-8-sig")
    print("저장:", OUT)


if __name__ == "__main__":
    main()
