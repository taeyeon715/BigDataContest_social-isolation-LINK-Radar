"""
한국사회보장정보원 복지서비스 목록 수집 → external/welfare/

- 중앙부처복지서비스 (NationalWelfareInformationsV001) 전체 목록
- 지자체복지서비스 (LocalGovernmentWelfareInformations): 서울특별시 · 강원특별자치도 전체 목록
- 인증키는 이 폴더의 .env 파일(KMA_API_KEY=...)에서 읽습니다(공공데이터포털 일반 인증키, 두 서비스 활용신청 필요).

실행: python fetch_welfare.py
"""
import os
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent
OUT = BASE / "external" / "welfare"
NATIONAL = "https://apis.data.go.kr/B554287/NationalWelfareInformationsV001/NationalWelfarelistV001"
LOCAL = "https://apis.data.go.kr/B554287/LocalGovernmentWelfareInformations/LcgvWelfarelist"


def load_key():
    key = os.environ.get("KMA_API_KEY")
    env = next((p for p in [BASE / ".env", BASE.parent / ".env"] if p.exists()), BASE / ".env")   # 패키지 폴더 또는 상위 폴더
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("KMA_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        sys.exit(".env 파일에 KMA_API_KEY=발급받은키 를 넣어 주세요.")
    return urllib.parse.unquote(key)


def fetch_all(url, params, key, rows=500):
    out, page = [], 1
    while True:
        q = urllib.parse.urlencode({"serviceKey": key, "pageNo": page, "numOfRows": rows, **params})
        root = ET.fromstring(urllib.request.urlopen(url + "?" + q, timeout=60).read())
        if root.findtext("resultCode") not in ("0", "00"):
            sys.exit(f"API 오류: {root.findtext('resultCode')} {root.findtext('resultMessage')}")
        items = [{c.tag: (c.text or "").strip() for c in s} for s in root.iter("servList")]
        out += items
        total = int(root.findtext("totalCount") or 0)
        if not items or len(out) >= total:
            return out
        page += 1
        time.sleep(0.3)


def main():
    key = load_key()
    OUT.mkdir(parents=True, exist_ok=True)
    nat = pd.DataFrame(fetch_all(NATIONAL, {"callTp": "L", "srchKeyCode": "003"}, key))
    nat = nat.rename(columns={"lifeArray": "생애주기", "intrsThemaArray": "관심주제", "trgterIndvdlArray": "가구상황"})
    nat.to_csv(OUT / "중앙부처복지서비스_목록.csv", index=False, encoding="utf-8-sig")
    print("중앙부처:", len(nat))
    loc = pd.concat([pd.DataFrame(fetch_all(LOCAL, {"ctpvNm": c}, key)) for c in ["서울특별시", "강원특별자치도"]], ignore_index=True)
    loc = loc.rename(columns={"lifeNmArray": "생애주기", "intrsThemaNmArray": "관심주제", "trgterIndvdlNmArray": "가구상황"})
    loc.to_csv(OUT / "지자체복지서비스_서울_강원_목록.csv", index=False, encoding="utf-8-sig")
    print("지자체:", len(loc), loc.groupby(["ctpvNm", "sggNm"]).size().loc[lambda s: s.index.get_level_values(1).isin(["강남구", "춘천시", ""])].to_dict())


if __name__ == "__main__":
    main()
