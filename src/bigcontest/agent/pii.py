"""자유 글의 사람 이름 찾기 (개인 지목 판단·저장 전 가리기, K-02).

규칙 기반이라 **모든 이름을 찾는다고 보장하지 않는다**. 찾는 모양:

1. 한글 이름 + 호칭: 성 + 1~2글자 이름 뒤에 할머니·할아버지·어르신·여사·선생님·씨·님 등 (띄어 써도 됨)
2. 한글 이름 + 받는 사람 조사: 성 + 1~2글자 이름 뒤에 바로 `에게·한테·께·께서·님·씨`
3. 한글 이름 + 일반 조사: 성 + 1~2글자 이름 뒤에 `의·이·가·은·는·을·를·와·과·랑·하고` 가 오고,
   같은 문장 뒤쪽에 연락·방문·전화·확인 같은 행동 말이 있을 때
4. 성으로 시작하는 세 글자 낱말이 홀로 있고 바로 앞뒤(12자 안)에 연락·방문·전화·확인·찾아·만나가 있을 때
5. 영문 이름: 대문자로 시작하는 두 낱말 중 한쪽이 흔한 한국 성 로마자(Kim Chulsoo, KIM Chul-soo, Chulsoo Kim)이거나
   뒤에 에게·한테·께·호칭이 붙은 것 (John Smith에게), Mr./Ms./Dr. + 이름
6. 성 + 씨 (김씨, 김 씨)

오탐을 줄이려고 지명(ref/adm_cd_names.csv 의 동 이름과 그 줄기, 도시 이름)과 성 글자로 시작하는 흔한 말
(이번·정도·한번·조사·고령·주민·이웃 …)은 이름으로 보지 않는다 (`NOT_NAMES`).
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

# 흔한 성 (약 100개, 한 글자) + 두 글자 성
SURNAMES_1 = ("김이박최정강조윤장임한오서신권황안송류유전홍고문양손배백허남심노하곽성차주우구민진나지엄채원천방공현함변"
              "염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예경봉라란")
SURNAMES_2 = ("남궁", "황보", "제갈", "선우", "독고", "사공", "서문")

HONORIFICS = ("할머니", "할아버지", "어르신", "어머님", "아버님", "어머니", "아버지", "선생님", "여사님", "여사", "할매", "할배",
              "씨", "님", "옹")
TO_PARTICLES = ("에게서", "에게", "한테서", "한테", "께서", "께")
WEAK_PARTICLES = ("이랑", "하고", "이가", "이는", "이를", "이의", "이도", "의", "이", "가", "은", "는", "을", "를", "와", "과",
                  "랑", "도")
ACTION_WORDS = ("연락", "방문", "전화", "확인", "찾아", "만나", "만날", "상담", "안부", "돌봐", "돌봄", "문자", "보내", "가봐",
                "가 봐", "챙겨", "연결", "사는", "사시", "살고", "주소", "혼자", "건강", "상태", "위험", "고립", "결제")
CONTACT_WORDS = ("연락", "방문", "전화", "확인", "찾아", "만나")
# 세 글자 홀로 쓴 말의 끝 글자가 이것이면 낱말 + 조사·어미로 봄 (예: 전화로, 확인해, 방문할)
HON_NOT_LAST = set("동구시군읍면리할한된될의들적던층별")
NOT_LAST = set("하로에서은는을를의도만해할한된될들적던층별과와고며면요게엔야다죠니까동구시군읍리가이랑내외간중용")

# 성 글자로 시작하지만 이름이 아닌 말 (지명·집단·흔한 말). 이름 후보(성 + 이름)와 정확히 같을 때 뺀다.
_COMMON = """
강남 강남구 강원 강원도 강북 강동 강서 강수 강도 강의 강조 강화 강제 강력
고령 고령층 고령자 고립 고독 고독사 고객 고민 고려 고정 고장 고향 고생 고용 고지 고통 고시 고유 고급 고속 고등
공휴 공휴일 공원 공간 공공 공동 공백 공급 공무원 공지 공식 공개 공유 공통 공단 공사 공부 공장 공연
구청 구역 구별 구간 구조 구성 구매 구입 구체 구분 구급 구급차 구독
국민 국가 국내 국외 국적 국어 국립
권역 권리 권장 권한
금요 금요일 금액 금융 금지 금년 금방
기준 기간 기초 기관 기록 기능 기본 기대 기대값 기술 기상 기온 기억 기타 기회 기존 기여 기업 기사 기준일 기획 기계
길이 길게 길가
김장 김장철 김밥 김치
나이 나중 나머지 나라 나눔 나들이
남성 남자 남편 남면 남산 남산면 남부 남쪽 남녀 남은 남아 남기 남김
노인 노인층 노인정 노년 노력 노출 노동 노후 노선
도시 도움 도로 도착 도구 도입 도곡 도내 도우미 도서관 도시락 도중 도표 도움말
명절 명단 명의 명확 명칭 명령
모두 모든 모임 모습 모양 모바일 모델 모집 모니터 모르 모레 모자
문의 문제 문서 문자 문화 문앞 문턱 문항 문구
민원 민간 민감
반증 반응 반영 반복 반대 반찬 반드시 반년 반나절
방문 방문자 방법 방금 방향 방식 방송 방안 방치 방역
배달 배송 배정 배분 배치 배경
백신 백화점 백분율 백만
변화 변경 변동 변수
봉사 봉사자 봉투
서비스 서울 서면 서류 서로 서쪽 서부 서명 서식 서둘러
석사 석사동 석달
선택 선생 선생님 선정 선별 선거 선물 선배 선순위
설날 설명 설정 설치 설문 설계
성별 성인 성과 성격 성공 성장 성인남 성인여 성연령 성비
소득 소양 소양동 소식 소비 소방 소개 소리 소속 소재 소통 소수 소외 소규모
손님 손자 손녀 손실
송년 송금
신호 신청 신고 신규 신북 신북읍 신동 신동면 신사 신사동 신사우 신사우동 신용 신분 신체 신경 신속 신뢰
심리 심야 심각 심사 심층
안부 안전 안내 안심 안정 안쪽 안에 안팎 안됨 안돼
양식 양로원 양쪽
어르신 어디 어떤 어떻게 어느 어제 어때 어려 어머니 어머님 어린이
엄마 엄청
여기 여성 여름 여부 여러 여유 여행 여건 여가 여전 여사
연락 연락처 연휴 연령 연령대 연말 연초 연결 연구 연도 연계 연속 연간 연장 연금
염려
예정 예상 예측 예산 예약 예방 예시 예외 예전 예년 예비
오늘 오전 오후 오래 오류 오른 오히려 오직 오프라인
옥상
왕래
용도 용어 용품
우리 우선 우편 우울 우려 우리집 우리동 우산
원인 원장 원래 원본 원칙 원거리 원하 원격
위험 위기 위치 위급 위원 위주 위해 위한 위탁
유형 유지 유의 유무 유동 유사 유효 유일 유선 유입
육아
은행
이번 이번주 이번달 이웃 이웃집 이후 이전 이상 이하 이용 이동 이탈 이유 이름 이미 이내 이외 이해 이것 이곳 이런 이렇게 이분 이분들 이야기 이사 이틀 이달 이날 이제 이어 이력 이행
인구 인원 인근 인력 인정 인식 인접 인터넷
임시 임대 임계 임계값
장애 장애인 장소 장기 장보기 장날 장례 장마
전화 전체 전에 전후 전국 전달 전문 전주 전년 전망 전부 전반 전담 전환 전용 전기 전월
정도 정기 정기적 정보 정책 정리 정상 정확 정해 정부 정의 정신 정신건강 정말 정식 정해진
제외 제공 제출 제도 제한 제대로 제일 제시 제때
조사 조건 조회 조운 조운동 조금 조치 조정 조기 조합 조심
주민 주민분 주변 주소 주의 주말 주간 주거 주기 주요 주로 주문 주택 주차 주의점 주민센터
지역 지역민 지난 지난주 지난달 지금 지원 지표 지도 지점 지정 지방 지급 지속 지출 지연 지인
진료 진짜 진행 진단
차이 차량 차례 차단
채널
천천히
최근 최소 최대 최종 최초 최신 최고 최저 최우선
추석 추가 추이 추세 추정 추적 추천 추위 추운
탁월
편의 편의점 편지 편이
표시 표본 표준 표현
하루 하나 하면 하는 하고 하지 하기 하는데 하나씩
한번 한파 한부모 한달 한주 한국 한글 한계 한도 한쪽 한낮 한밤 한꺼번에 한참 한정
함께
허용
현장 현재 현황 현금 현실
홍보 홍수
황금
마을 마음 마지막 마련 마트
어머 나오지 차상위 추리기 구성비 기준월 이미지 나은지 이분들께 한분
여러분 유족 유가족 이장 반장 구청장 기사 소장 계장 국장 조장 경찰 소방관 기관장 주임 주사 유관 위원장 주무관
""".split()


@lru_cache(maxsize=1)
def place_words() -> frozenset[str]:
    """지명: 동 이름(ref/adm_cd_names.csv)과 그 줄기(숫자·동/읍/면 뺀 것), 도시 이름."""
    out = {"강남", "강남구", "춘천", "춘천시", "서울", "서울시", "강원", "강원도", "부산", "대구", "인천", "광주", "대전", "울산"}
    path = Path(__file__).resolve().parents[3] / "ref" / "adm_cd_names.csv"
    try:
        with path.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                name = row.get("adm_nm") or ""
                out.add(name)
                stem = re.sub(r"\d*(동|읍|면)$", "", name)
                if stem:
                    out.add(stem)
                    out.add(stem + "동")
    except OSError:  # 참고 파일이 없어도 동작 (흔한 말 목록만 씀)
        pass
    return frozenset(out)


@lru_cache(maxsize=1)
def not_names(extra: tuple[str, ...] = ()) -> frozenset[str]:
    return frozenset(_COMMON) | place_words() | frozenset(extra)


_HANGUL = re.compile(r"[가-힣]")
_SUR = f"[{SURNAMES_1}]"
_SUR2 = "|".join(SURNAMES_2)
_START = re.compile(rf"(?<![가-힣0-9A-Za-z])(?:{_SUR2}|{_SUR})")
_SSI = re.compile(rf"(?<![가-힣0-9A-Za-z])(?:{_SUR2}|{_SUR})\s?씨(?![가-힣])")
_HON = re.compile(r"\s?(?:" + "|".join(HONORIFICS) + ")")
# 이름에 흔히 쓰는 글자. 호칭·받는 사람 조사가 없는 약한 모양(일반 조사, 홀로 쓴 세 글자)은 이름 두 글자가 모두 여기 들어야 함
NAME_SYLLABLES = set(
    "가강건경계고곤공관광교구국군권귀규균근금기길나남내녀년노다단달담대덕도동두득란래량례로록룡류률리린림마만말매명모목"
    "무묵문미민배백범병보복봉부분비빈사산삼상서석선설섭성세소솔송수숙순술승시식신실심아안애양억언업연열엽영예오옥온완왕"
    "용우운웅원월위유윤율은을음의이익인일임자작장재전정제조종주준중지직진질찬창채천철청초춘충치칠탁태택판팔평표필하학한"
    "해행향혁현혜호홍화환활황회효후훈휘흠희녕")
# 영문 이름: 흔한 한국 성 로마자 (앞이나 뒤 낱말이 이것이면 이름으로 봄)
EN_SURNAMES = {
    "Kim", "Lee", "Yi", "Park", "Pak", "Choi", "Choe", "Jung", "Jeong", "Chung", "Kang", "Cho", "Jo", "Yoon", "Yun",
    "Jang", "Chang", "Lim", "Im", "Han", "Oh", "Seo", "Suh", "Shin", "Sin", "Kwon", "Hwang", "Ahn", "An", "Song",
    "Ryu", "Yoo", "Yu", "Hong", "Ko", "Go", "Moon", "Mun", "Yang", "Son", "Sohn", "Bae", "Baek", "Paik", "Heo", "Huh",
    "Nam", "Shim", "Sim", "Noh", "Roh", "Ha", "Kwak", "Sung", "Seong", "Cha", "Joo", "Ju", "Woo", "Koo", "Ku", "Min",
    "Jin", "Na", "Ji", "Um", "Eom", "Chae", "Won", "Chun", "Cheon", "Bang", "Kong", "Hyun", "Ham", "Byun", "Yeom",
    "Choo", "Do", "Seok", "Sun", "Seol", "Ma", "Gil", "Yeon", "Wi", "Pyo", "Myung", "Ki", "Ban", "Wang", "Geum",
    "Ok", "Yook", "Maeng", "Je", "Mo", "Tak", "Kook", "Eo", "Eun", "Yong", "Ye", "Kyung", "Bong"}
_EN = re.compile(r"(?<![A-Za-z])((?:[A-Z][a-z]+|[A-Z]{2,})\s+[A-Z][a-z]+(?:-[A-Za-z][a-z]+)?)(?![A-Za-z])")
_EN_TITLE = re.compile(r"(?<![A-Za-z])(?:Mr|Ms|Mrs|Dr)\.?\s+[A-Z][a-z]+(?![A-Za-z])")
_EN_AFTER = re.compile(r"\s?(?:에게|한테|께|" + "|".join(HONORIFICS) + ")")
_CLAUSE_END = re.compile(r"[.!?\n,;]")


def _clause_after(text: str, i: int, n: int = 25) -> str:
    rest = text[i:i + n]
    m = _CLAUSE_END.search(rest)
    return rest[: m.start()] if m else rest


def _clause_before(text: str, i: int, n: int = 12) -> str:
    rest = text[max(0, i - n):i]
    m = list(_CLAUSE_END.finditer(rest))
    return rest[m[-1].end():] if m else rest


def _has(words, s: str) -> bool:
    return any(w in s for w in words)


def _namelike(given: str) -> bool:
    return len(given) == 2 and all(c in NAME_SYLLABLES for c in given) and given[-1] not in NOT_LAST


def _korean_spans(text: str, skip: frozenset[str]) -> list[tuple[int, int]]:
    """한글 이름 자리 (시작, 끝). 이름 부분만 (호칭·조사 제외)."""
    out: list[tuple[int, int]] = []
    for m in _START.finditer(text):
        s, sur_end = m.start(), m.end()
        for glen in (2, 1):
            e = sur_end + glen
            given = text[sur_end:e]
            if len(given) != glen or not all(_HANGUL.match(c) for c in given):
                continue
            name = text[s:e]
            if name in skip or given[-1] in "님씨":  # "이장님께" → 이름 아님 ("이장" 은 흔한 말)
                continue
            after = text[e:]
            nxt = after[:1]
            # 1. 호칭 (띄어 써도 됨). 끝 글자가 동·구·할·한 같은 말(지명·꾸밈말)은 뺌
            if _HON.match(after) and name[-1] not in HON_NOT_LAST:
                out.append((s, e))
                break
            # 2. 받는 사람 조사 (에게·한테·께)
            if any(after.startswith(p) for p in TO_PARTICLES):
                out.append((s, e))
                break
            if not _namelike(given):
                continue
            # 3. 일반 조사 + 같은 문장 뒤쪽에 행동 말 (세 글자 이름만)
            p = next((p for p in WEAK_PARTICLES if after.startswith(p)), None)
            if p and not _HANGUL.match(after[len(p):][:1] or "") \
                    and _has(ACTION_WORDS, _clause_after(text, e + len(p))):
                out.append((s, e))
                break
            # 4. 세 글자 낱말이 홀로 있고 같은 문장 앞뒤 12자 안에 연락·방문·전화·확인·찾아·만나
            if not _HANGUL.match(nxt or "") and _has(CONTACT_WORDS, _clause_before(text, s) + " " + _clause_after(text, e, 12)):
                out.append((s, e))
                break
    return out


def _english_spans(text: str) -> list[tuple[int, int]]:
    out = []
    for m in _EN.finditer(text):
        words = re.split(r"\s+", m.group(1))
        cap = {w.capitalize() for w in (words[0], words[-1])}
        after = text[m.end():]
        if cap & EN_SURNAMES or _EN_AFTER.match(after):
            out.append((m.start(1), m.end(1)))
    for m in _EN_TITLE.finditer(text):
        out.append((m.start(), m.end()))
    return out


def find_names(text: str | None, extra_not_names: tuple[str, ...] = ()) -> list[str]:
    """글 속 사람 이름 표현 (겹치지 않게, 나온 순서). 이름 부분만 돌려준다 (호칭·조사 제외)."""
    if not text:
        return []
    skip = not_names(tuple(extra_not_names))
    spans = _korean_spans(text, skip) + _english_spans(text)
    for m in _SSI.finditer(text):
        spans.append((m.start(), m.end()))
    spans.sort(key=lambda x: (x[0], -x[1]))
    out, last = [], -1
    for s, e in spans:
        if s >= last:
            out.append(text[s:e])
            last = e
    return list(dict.fromkeys(out))


def mask_names(text: str, extra_not_names: tuple[str, ...] = ()) -> tuple[str, int]:
    """이름 자리를 [이름] 으로. (가린 글, 가린 개수)."""
    names = sorted(find_names(text, extra_not_names), key=len, reverse=True)
    n = 0
    for name in names:
        k = text.count(name)
        if k:
            text = text.replace(name, "[이름]")
            n += k
    return text, n
