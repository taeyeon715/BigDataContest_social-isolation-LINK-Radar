from pathlib import Path
import nbformat as nbf
import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent)); import flow

OUT = Path(__file__).resolve().parent.parent / '05_복지자원_매핑.ipynb'
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md(r"""
# 05
**질문**: 04에서 나온 위험 유형(이중 위험 · 행동형 · 취약형)과 생애단계별로 **연계할 복지서비스가 실제로 있는가**, 있다면 무엇을 먼저 추천할 것인가.

| 재료 | 출처 |
|---|---|
| 위험 유형 · 단계 · 주요 요인 | 04 결과 `outputs/위험도_2축_강남_춘천_동x성x연령.csv` |
| 중앙부처 복지서비스 461개 | 한국사회보장정보원 중앙부처복지서비스 API (`fetch_welfare.py`) |
| 지자체 복지서비스 (서울시·강남구, 강원도·춘천시) | 한국사회보장정보원 지자체복지서비스 API |
""")
code(r"""
import warnings, re; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'figure.dpi': 110,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'axes.axisbelow': True,
    'grid.color': '#e6e5e1', 'grid.linewidth': .6, 'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e',
    'xtick.color': '#52514e', 'ytick.color': '#52514e', 'axes.titlesize': 11.5, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left', 'legend.frameon': False})
pd.set_option('display.max_columns', 30); pd.set_option('display.width', 220); pd.set_option('display.max_colwidth', 60)
EXT, OUTD = Path('external'), Path('outputs')
MAIN = ['서울 강남구', '강원 춘천시']; AGE5 = ['20대', '30대', '40대', '50대', '60대+']
R = pd.read_csv(OUTD / '위험도_2축_강남_춘천_동x성x연령.csv', dtype={'SGIS코드': str})
nat = pd.read_csv(EXT / 'welfare/중앙부처복지서비스_목록.csv').assign(제공='중앙부처', ctpvNm=np.nan, sggNm=np.nan)
loc = pd.read_csv(EXT / 'welfare/지자체복지서비스_서울_강원_목록.csv').assign(제공='지자체')
S = pd.concat([nat, loc], ignore_index=True)
S['본문'] = S['servNm'].fillna('') + ' ' + S['servDgst'].fillna('')
print('서비스 수:', S.groupby('제공').size().to_dict())
""")

md("## 1. 지역에서 실제로 쓸 수 있는 서비스 모으기")
md(r"""
- 강남 주민이 쓸 수 있는 것 = 중앙부처 + 서울시 + 강남구 / 춘천 주민 = 중앙부처 + 강원도 + 춘천시
- 분석 대상이 20세 이상이므로, 생애주기가 영유아·아동·청소년·임신출산**만**인 서비스는 뺍니다. 생애주기가 비어 있으면 '전 연령'으로 둡니다.
- 보훈·다문화·한부모·장애 등록처럼 **특정 자격이 있어야 받는 서비스**도 뺍니다. 우리 데이터는 집단 단위라 그런 자격을 알 수 없기 때문입니다.
""")
code(r"""
def avail(region):
    ctpv, sgg = {'서울 강남구': ('서울특별시', '강남구'), '강원 춘천시': ('강원특별자치도', '춘천시')}[region]
    m = (S['제공'] == '중앙부처') | ((S['ctpvNm'] == ctpv) & (S['sggNm'].isna() | (S['sggNm'] == sgg)))
    d = S[m].copy(); d['지역'] = region
    d['수준'] = np.where(d['제공'] == '중앙부처', '중앙', np.where(d['sggNm'].isna(), '시도', '시군구'))
    return d
A = pd.concat([avail(r) for r in MAIN], ignore_index=True)
life = A['생애주기'].fillna('').str.replace(' ', '').str.split(',')
A['청년'] = life.map(lambda l: '청년' in l); A['중장년'] = life.map(lambda l: '중장년' in l); A['노년'] = life.map(lambda l: '노년' in l)
A['전연령'] = A['생애주기'].isna()
A = A[A[['청년', '중장년', '노년', '전연령']].any(axis=1)].copy()          # 성인 대상이 아닌 서비스 제외
# 특정 자격(보훈·다문화·한부모·청소년·장애 등)이 있어야 받는 서비스는 집단 단위로 추천할 수 없어 뺌 (우리 데이터에 그 정보가 없음)
SPEC = r'청소년|아동|영유아|출산|임신|위안부|유공|보훈|다문화|탈북|귀국|입양|위탁|한부모|조손|다자녀|군인|제대|농어|어업|난임'
A['특정 자격'] = A['servNm'].str.contains(SPEC) | (A['servNm'].str.contains('장애') & ~A['servNm'].str.contains('독거'))
print('특정 자격 대상으로 제외:', A.groupby('지역')['특정 자격'].sum().to_dict())
A = A[~A['특정 자격']].copy()
print('지역에서 쓸 수 있는 성인 대상 서비스'); display(A.groupby(['지역', '수준']).size().unstack())
""")

md("## 2. 서비스를 '고립 대응 기능'으로 분류")
md(r"""
복지로의 관심주제 코드와 서비스 이름·설명의 핵심어로 네 기능에 배정합니다. 한 서비스가 여러 기능에 들 수 있습니다.

| 기능 | 연결되는 위험 유형 | 관심주제 | 핵심어 예 |
|---|---|---|---|
| **관계·정서** | 행동형 (동네에 갇힘) | 정신건강, 문화·여가 | 고립, 외로움, 은둔, 마음, 상담, 사회참여, 여가 |
| **경제·생활** | 취약형 (혼자 살고 경제적으로 어려움) | 생활지원, 서민금융, 주거, 일자리, 에너지 | 생계, 주거, 긴급복지, 수급, 저소득 |
| **돌봄·안전** | 이중 위험 (사례관리) | 보호·돌봄, 안전·위기 | 돌봄, 안부, 방문, 사례관리, 고독사, 응급, 독거 |
| 건강 | 공통 보조 | 신체건강 | 건강, 의료, 검진 |

**고립 직접 대응** = 이름·설명에 '고립·고독·외로·은둔·1인 가구·독거·안부'가 들어간 서비스입니다.
""")
code(r"""
FUNC = {'관계·정서': (['정신건강', '문화·여가'], ['고립', '고독', '외로움', '은둔', '관계', '사회참여', '커뮤니티', '동아리', '마음', '우울', '상담', '심리', '자살', '여가', '모임', '공동체']),
        '경제·생활': (['생활지원', '서민금융', '주거', '일자리', '에너지'], ['생계', '주거', '임대', '월세', '일자리', '취업', '긴급복지', '기초생활', '수급', '저소득', '차상위', '생활비']),
        '돌봄·안전': (['보호·돌봄', '안전·위기'], ['돌봄', '안부', '방문', '사례관리', '고독사', '응급', '위기', '독거', '말벗', '도시락', '반찬']),
        '건강': (['신체건강'], ['건강', '의료', '검진', '치료'])}
DIRECT = r'고립|고독|외로움|외롭|은둔|1인 ?가구|독거|(?<!위)안부'          # '위안부'·'국외로' 같은 오탐 방지
theme = A['관심주제'].fillna('').str.replace(' ', '')
for f, (th, kw) in FUNC.items():
    A[f] = theme.map(lambda t: any(x in t.split(',') for x in th)) | A['본문'].str.contains('|'.join(kw))
A['고립 직접'] = A['본문'].str.contains(DIRECT)
A['저소득 대상'] = A['가구상황'].fillna('').str.contains('저소득') | A['본문'].str.contains('저소득|기초생활|차상위|수급')
F = list(FUNC)
print('기능별 서비스 수 (지역별, 중복 포함)'); display(A.groupby('지역')[F + ['고립 직접']].sum())
print('고립 직접 대응 서비스'); display(A[A['고립 직접']][['지역', '수준', 'servNm', '생애주기', '관심주제']].sort_values(['지역', '수준']))
""")

md("## 3. 공급 공백: 위험 인구에 비해 서비스가 비는 곳")
code(r"""
STAGE = {'20대': '청년', '30대': '청년', '40대': '중장년', '50대': '중장년', '60대+': '노년'}
rows = []
for reg in MAIN:
    a = A[A['지역'] == reg]
    for st in ['청년', '중장년', '노년']:
        s = a[a[st] | a['전연령']]
        r = R[(R['지역'] == reg) & (R['연령5'].map(STAGE) == st)]
        rows.append({'지역': reg, '생애단계': st, **{f: int(s[f].sum()) for f in F}, '고립 직접': int(s['고립 직접'].sum()),
                     '지역 시군구 자체(관계·정서)': int(s[(s['수준'] == '시군구') & s['관계·정서']].shape[0]),
                     '이중 위험 인구': int(r.loc[r['위험 유형'] == '이중 위험', '주민'].sum()),
                     '행동형 인구': int(r.loc[r['위험 유형'] == '행동형', '주민'].sum()),
                     '취약형 인구': int(r.loc[r['위험 유형'] == '취약형', '주민'].sum())})
GAP = pd.DataFrame(rows).set_index(['지역', '생애단계']); display(GAP)
GAP.to_csv(OUTD / '복지자원_공급공백_생애단계.csv', encoding='utf-8-sig')
fig, axes = plt.subplots(1, 2, figsize=(13, 3.6), sharey=True)
for ax, reg in zip(axes, MAIN):
    g = GAP.loc[reg][F]; x = np.arange(len(g.index)); wd = .2
    for i, (f, c) in enumerate(zip(F, ['#eb6834', '#1c5cab', '#c43a39', '#b5b4ae'])):
        ax.bar(x + (i - 1.5) * wd, g[f], width=wd, color=c, label=f)
    ax.set_xticks(x, g.index); ax.set_title(f'{reg}: 생애단계별 쓸 수 있는 서비스 수 (기능별)')
axes[0].legend(fontsize=8.5); plt.tight_layout(); plt.show()
""")

md("## 4. 유형 × 생애단계별 추천 규칙과 결과")
md(r"""
**추천 점수** (규칙 기반, 설명 가능하도록 단순하게)
- 유형에 필요한 기능을 맞히면 점수를 줍니다.
  - 이중 위험: 돌봄·안전 3, 경제·생활 2, 관계·정서 1
  - 행동형: 관계·정서 3, 건강 1
  - 취약형: 경제·생활 3, 돌봄·안전 2
  - 저위험: 관계·정서 1 (예방 안내)
- 가점:
  - 고립 직접 대응 +2
  - 생애단계 명시 +1 (전 연령은 0)
  - 가까운 행정 수준: 시군구 +1, 시도 +0.5 (현장 연계가 쉬움)
  - 집단의 주요 요인에 '공공부조 수급'이 있고 서비스가 저소득 대상이면 +1
""")
code(r"""
NEED = {'이중 위험': {'돌봄·안전': 3, '경제·생활': 2, '관계·정서': 1}, '행동형': {'관계·정서': 3, '건강': 1},
        '취약형': {'경제·생활': 3, '돌봄·안전': 2}, '저위험': {'관계·정서': 1}}
def recommend(region, age, typ, poor=False, k=3):
    st = STAGE[age]; a = A[(A['지역'] == region) & (A[st] | A['전연령'])].copy()
    a['점수'] = sum(w * a[f] for f, w in NEED[typ].items())
    a = a[a['점수'] > 0]
    a['점수'] += 2 * a['고립 직접'] + a[st].astype(int) + a['수준'].map({'시군구': 1, '시도': .5, '중앙': 0})
    if poor: a['점수'] += a['저소득 대상'].astype(int)
    a = a.sort_values(['점수', 'inqNum'], ascending=[False, False])           # 같은 점수면 조회 수가 많은(알려진) 서비스 우선
    return a.head(k)[['servNm', '수준', '점수', 'servDtlLink']]
REC = []
for reg in MAIN:
    for age in AGE5:
        for typ in NEED:
            for rank, (_, s) in enumerate(recommend(reg, age, typ, k=5).iterrows(), 1):
                REC.append({'지역': reg, '연령5': age, '위험 유형': typ, '순위': rank, '서비스': s['servNm'], '수준': s['수준'], '점수': s['점수'], '링크': s['servDtlLink']})
REC = pd.DataFrame(REC); REC.to_csv(OUTD / '복지자원_추천_유형x생애단계.csv', index=False, encoding='utf-8-sig')
for reg in MAIN:
    print(f'[{reg}] 유형 × 생애단계별 1~3순위')
    t = REC[(REC['지역'] == reg) & (REC['순위'] <= 3) & (REC['위험 유형'] != '저위험')]
    display(t.groupby(['위험 유형', '연령5'])['서비스'].apply(lambda s: ' / '.join(s)).unstack(0).reindex(AGE5))
""")

md("## 5. 우선 지원 대상에 서비스 붙이기 (에이전트 '추천' 단계의 입력)")
code(r"""
P = R[R['단계'].isin(['심각', '경계']) | R['생애단계 내 단계'].isin(['심각'])].copy()
recs = []
for _, r in P.iterrows():
    poor = '공공부조' in str(r['주요 요인'])
    s = recommend(r['지역'], r['연령5'], r['위험 유형'], poor=poor, k=3)
    recs.append(' / '.join(s['servNm']))
P['추천 서비스 (1~3순위)'] = recs
P = P.sort_values(['지역', '우선순위 점수'], ascending=[True, False])
cols = ['지역', '읍면동명', '성별', '연령5', '주민', '위험 유형', '단계', '생애단계 내 단계', '주요 요인', '추천 서비스 (1~3순위)']
P[cols].to_csv(OUTD / '우선대상_추천서비스.csv', index=False, encoding='utf-8-sig')
print('우선 대상 집단 수:', P.groupby('지역').size().to_dict())
for reg in MAIN: display(P[P['지역'] == reg][cols].head(6))
""")
md("## 6. 요약")
SUM = Path(__file__).with_name('s5.md')
md(SUM.read_text(encoding='utf-8') if SUM.exists() else '_(실행 결과를 바탕으로 작성)_')

if hasattr(flow, 'FLOW5'):
    flow.annotate(cells, flow.FLOW5, flow.TITLE5)
nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}})
nbf.write(nb, OUT); print('written', OUT, len(cells))
