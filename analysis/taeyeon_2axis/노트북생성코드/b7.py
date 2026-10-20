from pathlib import Path
import nbformat as nbf
import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent)); import flow

OUT = Path(__file__).resolve().parent.parent / '07_가설검증_전연령고립.ipynb'
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md(r"""
# 07
**질문**: "고립은 노년만의 문제가 아니다"는 데이터로도 확인되는가?

이 프로젝트는 처음부터 전 연령을 분석했습니다. 이 노트북은 그 출발점을 **가설로 세우고, 실행 전에 고정한 기준으로 판정**합니다.
""")

md("## 1. 배경 — 왜 이 가설을 세웠나")
md(r"""
| 배경 | 내용 | 출처 |
|---|---|---|
| 청년 | 보건복지부는 고립·은둔 청년을 청년 인구의 약 5%, **54만 명**으로 추정했습니다 (2022 청년 삶 실태조사 기반). | [뉴시스, "'54만명 추정' 고립·은둔 청년, 국가가 나선다" (2023.12.13)](https://www.newsis.com/view/NISX20231213_0002556536) |
| 중년 | 2023년 고독사 사망자 3,661명 중 **50·60대 남성이 53.9%**입니다. | [이투데이 (보건복지부 고독사 실태조사 보도)](https://www.etoday.co.kr/news/view/2410394) |
| 공식 통계 | 사회적 고립도는 60세 이상 39.4%로 가장 높지만, 19~29세도 24.4%입니다. | [통계청 국가지표체계](https://www.index.go.kr/unify/idx-info.do?idxCd=8080) |

정책과 인식은 여전히 '독거노인' 중심이지만, 위 자료는 청년·중년에도 고립 위험이 크다는 것을 보여 줍니다. 우리 지역(강남·춘천)의 데이터로도 그런지 확인합니다.
""")

md("## 2. 가설과 판정 기준 (실행 전 고정)")
code(r"""
import warnings, json, re; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt
plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'figure.dpi': 110,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'axes.axisbelow': True,
    'grid.color': '#e6e5e1', 'grid.linewidth': .6, 'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e',
    'xtick.color': '#52514e', 'ytick.color': '#52514e', 'axes.titlesize': 10.5, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left', 'legend.frameon': False})
pd.set_option('display.max_columns', 30); pd.set_option('display.width', 220)
DATA, EXT, OUTD = Path('csv'), Path('external'), Path('outputs')
MAIN = ['서울 강남구', '강원 춘천시']; AGE5 = ['20대', '30대', '40대', '50대', '60대+']
STAGE = {'20대': '청년', '30대': '청년', '40대': '중년', '50대': '중년', '60대+': '노년'}
STAGE_C = {'청년': '#2a78d6', '중년': '#1baf7e', '노년': '#eb6834'}
PRE = json.load(open(OUTD / '가설_전연령고립_사전등록.json', encoding='utf-8'))
FROZEN = json.load(open(OUTD / '모델설정_동결.json', encoding='utf-8'))
for k in ['가설', '판정 규칙']:
    print(f'[{k}]'); [print(f'  {a}: {b}') for a, b in PRE[k].items()]
def a5(a): return '60대+' if a >= 60 else (f'{a // 10 * 10}대' if a >= 20 else None)
""")

md(r"""
**근거로 쓰지 않는 것**
- 04의 위험 유형 비중(예: 20대 이중 위험 23~24%)은 쓰지 않습니다. H·V를 **같은 성·연령 안의 백분위**로 만들었기 때문에, 어느 연령이든 상위권 비중이 기계적으로 비슷하게 나옵니다. 이 값으로 "청년도 노년만큼 위험하다"고 하면 순환 논리입니다(6절에서 확인).
- 서울 관심집단 비율도 쓰지 않습니다. 연령대마다 상위 2%로 정의되어 있어 연령 간 수준을 비교할 수 없습니다.

→ **연령 간 절대 수준**을 비교할 수 있는 지표만 씁니다.
""")

md("## 3. 증거 지표 계산")
code(r"""
%%time
E = []                                                   # 행: 지표 × 지역 × 연령
def add(key, region, name, direction, vals, src):
    for a, v in vals.items(): E.append({'지표': key, '지역': region, '내용': name, '위험 방향': direction, '연령': a, '값': float(v), '출처': src})
# E1 공식 사회적 고립도 (전국)
add('E1', '전국', '공식 사회적 고립도(%)', '높을수록', FROZEN['기본위험 B(연령별 %)'], '통계청 사회조사 2025')
# E2 혼자 사는 비율 (주민등록 2024.12)
CODES = pd.read_csv(EXT / 'codes/읍면동_코드대응표_강남_춘천.csv', dtype=str)
K2R = dict(zip(CODES['행정기관코드'], CODES['지역']))
def mois(kind):
    d = pd.read_csv(EXT / f'mois/행정동_성연령별_{kind}_20241231.csv', encoding='cp949', dtype=str)
    d['지역'] = d['행정구역'].str.extract(r'\((\d{10})\)')[0].map(K2R); d = d.dropna(subset=['지역'])
    cols = [c for c in d.columns if re.search(r'_(남|여)_\d', c)]
    L = d.melt(id_vars='지역', value_vars=cols, var_name='col', value_name='n')
    L['n'] = pd.to_numeric(L['n'].str.replace(',', '')); L['성별'] = L['col'].str.extract(r'_(남|여)_')[0]
    L['연령5'] = L['col'].str.extract(r'_(남|여)_(\d+)')[1].astype(int).map(a5)
    return L.dropna(subset=['연령5'])
pop, one = mois('주민등록인구'), mois('1인세대수')
alone = (one.groupby(['지역', '연령5'])['n'].sum() / pop.groupby(['지역', '연령5'])['n'].sum() * 100)
alone_sex = (one.groupby(['지역', '성별', '연령5'])['n'].sum() / pop.groupby(['지역', '성별', '연령5'])['n'].sum() * 100)
for r in MAIN: add('E2', r, '혼자 사는 비율(%)', '높을수록', alone[r], '주민등록 2024.12')
# E3 카드 (법인·특수 업종 제외, 04와 같은 정의)
REN = {'기준일자': '일자', '가맹점 위치정보(시·군·구)': '지역', '가맹점 업종': '업종', '이용고객 성별': '성별', '이용고객 연령대': '연령대', '결제건수 합계': '건수'}
EXCL = ['ZZ_나머지', '세금공과금', '보험', '상품권/복권', '통신요금(이동,시내전화)', '통신요금(PC통신,무선호출)', '학교등록금', '결제대행(PG)']
OUTD_ = ['한식', '양식', '일식', '중식', '기타요식', '패스트푸드', '제과점', '커피전문점', '노래방', '영화/공연', '게임방/오락실', '종합레저타운/놀이동산',
         '스포츠시설', '헬스장', '실내/실외골프장', '싸우나/목욕탕', '유흥업소', '주류판매', '여행사/항공사', '호텔/콘도', '고속버스/철도/여객선', '택시']
card = pd.read_csv(DATA / '신한카드_데이터1_시간대별.csv', usecols=list(REN) + ['시간대']).rename(columns=REN)
card = card[(card['성별'] != '법인') & ~card['업종'].isin(EXCL)]
card['연령5'] = card['연령대'].str.replace(' ', '').map(lambda a: None if a == '10대' else ('60대+' if a in ('60대', '70대', '80대', '90대') else a))
card = card.dropna(subset=['연령5']); k = ['지역', '연령5']; base = card.groupby(k)['건수'].sum()
night = card[card['시간대'] == '00_05'].groupby(k)['건수'].sum() / base * 100
outdoor = card[card['업종'].isin(OUTD_)].groupby(k)['건수'].sum() / base * 100
age = pd.read_csv(DATA / 'SK_유동인구_성연령.csv', dtype={'소지역코드': str})
age['지역'] = age['소지역코드'].str[:8].map(dict(zip(CODES['SGIS코드'], CODES['지역']))); age = age.dropna(subset=['지역'])
SKA = ['10대', '20대', '30대', '40대', '50대', '60대+']; MC = [c for c in age.columns if c.startswith('남자')]; FC = [c for c in age.columns if c.startswith('여자')]
flow_ = pd.concat({a: age.groupby('지역')[[MC[i], FC[i]]].sum().sum(axis=1) for i, a in enumerate(SKA) if a != '10대'}, names=['연령5']).swaplevel().sort_index()
part = (base / base.groupby('지역').transform('sum')) / (flow_ / flow_.groupby('지역').transform('sum'))
for r in MAIN:
    add('E3a', r, '심야(00~05시) 소비 비중(%)', '높을수록', night[r], '신한카드')
    add('E3b', r, '외부활동 소비 비중(%)', '낮을수록', outdoor[r], '신한카드')
    add('E3c', r, '소비 참여도(카드 비중 ÷ 유동 비중)', '낮을수록', part[r], '신한카드 + SK')
# E4 고독사 위험군 규모 (복지부 2022 조사 보도: 50대 37.3만, 60대 35.5만)
add('E4', '전국', '고독사 위험군 규모(만 명, 50대 vs 60대)', '높을수록', {'50대': 37.3, '60대+': 35.5}, '복지부 2022 조사 보도 (뉴스토마토)')
E = pd.DataFrame(E)
TAB = E.pivot_table(index=['지표', '지역', '내용', '위험 방향'], columns='연령', values='값').reindex(columns=AGE5)
TAB.round(2)
""")

md("## 4. 판정 — 지표마다 '청년·중년 중 노년과 같거나 더 위험한 연령대가 있는가'")
code(r"""
rows = []
for (key, reg, name, d), r in TAB.iterrows():
    elder = r['60대+']; others = r.drop('60대+').dropna()
    worse = others[others >= elder] if d == '높을수록' else others[others <= elder]
    rows.append({'지표': key, '지역': reg, '내용': name, '위험 방향': d, '노년(60대+)': round(elder, 2),
                 '청년·중년 중 가장 위험한 값': round((others.max() if d == '높을수록' else others.min()), 2),
                 '노년 이상인 연령대': ', '.join(worse.index) or '—', 'H1 성립': len(worse) > 0})
J = pd.DataFrame(rows)
n_ok, n = int(J['H1 성립'].sum()), len(J)
verdict = '지지' if n_ok >= int(np.ceil(n * 2 / 3)) else ('부분 지지' if n_ok >= 1 else '기각')
display(J)
print(f'판정 지표 {n}개 중 H1 성립 {n_ok}개 → 종합 판정: {verdict}')
J.assign(종합판정=verdict).to_csv(OUTD / '가설검증_전연령고립.csv', index=False, encoding='utf-8-sig')
""")
code(r"""
keys = list(TAB.index)
fig, axes = plt.subplots(2, 5, figsize=(17, 6.6))
for ax, key in zip(axes.flat, keys):
    r = TAB.loc[key].dropna(); x = np.arange(len(r))
    ax.bar(x, r.values, color=[STAGE_C[STAGE[a]] for a in r.index], width=.65)
    ax.axhline(r['60대+'], color=STAGE_C['노년'], lw=1, ls='--')
    ok = J[(J['지표'] == key[0]) & (J['지역'] == key[1])]['H1 성립'].iloc[0]
    ax.set_xticks(x, r.index, fontsize=8); ax.tick_params(axis='y', labelsize=8)
    ax.set_title(f"{key[0]} {key[1].replace('서울 ', '').replace('강원 ', '')} — {'H1 성립' if ok else 'H1 불성립'}\n{key[2]} ({key[3]} 위험)", fontsize=9)
for ax in axes.flat[len(keys):]: ax.axis('off')
handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in STAGE_C.values()]
fig.legend(handles, list(STAGE_C), loc='lower center', ncol=3, fontsize=9); plt.tight_layout(rect=(0, .05, 1, 1)); plt.show()
""")
code(r"""
print('참고: 성별로 나눈 혼자 사는 비율(%) — 남녀 모두에서 같은 패턴인지')
display(alone_sex.unstack('연령5').reindex(columns=AGE5).round(1))
""")

md("## 5. 보조 — 고립 대응 서비스는 연령별로 어떻게 공급되나 (판정 제외)")
code(r"""
G = pd.read_csv(OUTD / '복지자원_공급공백_생애단계.csv')
G['위험 인구(이중 위험+행동형)'] = G['이중 위험 인구'] + G['행동형 인구']
display(G[['지역', '생애단계', '고립 직접', '관계·정서', '위험 인구(이중 위험+행동형)']])
""")

md("## 6. 왜 04의 유형 비중은 근거가 될 수 없나 (확인)")
code(r"""
R = pd.read_csv(OUTD / '위험도_2축_강남_춘천_동x성x연령.csv')
def top_share(g, col):                               # 지역 안 상위 1/3(인구 가중)에 드는 인구 비중
    return g.groupby('연령5').apply(lambda x: (x[col] >= R.loc[g.index, col].quantile(2 / 3)).mean() * 100)
chk = pd.concat({reg: top_share(R[R['지역'] == reg], '적용 H') for reg in MAIN}, axis=1).reindex(AGE5)
print("행동 고립 H가 지역 상위 1/3에 드는 집단 비중(%) — 연령마다 비슷하게 나오는 것은 설계 때문"); display(chk.round(1))
""")
md("## 7. 요약")
SUM = Path(__file__).with_name('s7.md')
md(SUM.read_text(encoding='utf-8') if SUM.exists() else '_(실행 결과를 바탕으로 작성)_')

if hasattr(flow, 'FLOW7'):
    flow.annotate(cells, flow.FLOW7, flow.TITLE7)
nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}})
nbf.write(nb, OUT); print('written', OUT, len(cells))
