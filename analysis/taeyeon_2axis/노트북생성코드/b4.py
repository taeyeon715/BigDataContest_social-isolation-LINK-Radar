from pathlib import Path
import nbformat as nbf
import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent)); import flow

OUT = Path(__file__).resolve().parent.parent / '부록A_학습형_비교모델.ipynb'
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md(r"""
# 04
**위험도의 구조** — 네 축 중 세 축을 제공 데이터가 맡고, 외부 데이터는 기준점·보정·검증만 맡습니다.

| 축 | 질문 | 담당 데이터 | 노트북 기호 |
|---|---|---|---|
| 기본 위험 | 이 성·연령은 원래 얼마나 고립되기 쉬운가 | 외부: 통계청 사회적 고립도 | **B** |
| 구조 위험 | 이 동은 사는 사람 구성상 위험한가 | 외부: 행안부 주민등록 (서울 전체 417개 동으로 학습) | **S** |
| 행동 위험 (어디서) | 이 동·집단은 머무는 방식이 고립형인가 | **제공: SK 유동인구** | **A** |
| 집단 행동 (누가) | 이 성·연령 집단은 생활 소비가 위축됐는가 | **제공: 신한카드** | **C** |
| 시점 신호 (언제) | 지금 평소보다 활동이 지속적으로 줄었는가 | **제공: 신한카드 일별** + 외부: 기상청 보정 | **T** (단계 조정 규칙) |

**위험도 = B × (S·A) × C** (동 × 성 × 연령 단위; S와 A는 강남 정답으로 함께 학습) → 공식 고립률에 맞춘 기준점으로 **5단계** → T로 실시간 단계 조정
""")

code(r"""
import warnings, json, re; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np, pandas as pd
import matplotlib.pyplot as plt, matplotlib.ticker as mtick
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from scipy.stats import spearmanr
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import make_pipeline
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut

plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'figure.dpi': 110,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'axes.axisbelow': True,
    'grid.color': '#e6e5e1', 'grid.linewidth': .6, 'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e',
    'xtick.color': '#52514e', 'ytick.color': '#52514e', 'axes.titlesize': 11.5, 'axes.titleweight': 'bold',
    'axes.titlelocation': 'left', 'legend.frameon': False, 'lines.linewidth': 2})
pd.set_option('display.max_columns', 60); pd.set_option('display.max_rows', 120); pd.set_option('display.width', 220)
pd.set_option('display.float_format', '{:,.3f}'.format)
DATA, EXT, OUTD = Path('csv'), Path('external'), Path('outputs'); OUTD.mkdir(exist_ok=True)
MAIN = ['서울 강남구', '강원 춘천시']
C_ = {'서울 강남구': '#2a78d6', '강원 춘천시': '#eb6834'}
GRAY, INK, INK2 = '#b5b4ae', '#0b0b0b', '#52514e'
SEQ = LinearSegmentedColormap.from_list('seq', ['#f4f8fd', '#9ec5f4', '#3987e5', '#1c5cab', '#0d366b'])
LEVEL_C = ['#e6e5e1', '#9ec5f4', '#eda100', '#eb6834', '#c43a39']       # 정상·관심·주의·경계·심각
AGE5 = ['20대', '30대', '40대', '50대', '60대+']; SEX = ['남성', '여성']
def heat(ax, df, cmap=SEQ, fmt='{:.2f}', vmin=None, vmax=None, fs=8, rot=0):
    v = df.values.astype(float); norm = plt.Normalize(np.nanmin(v) if vmin is None else vmin, np.nanmax(v) if vmax is None else vmax)
    ax.imshow(v, cmap=cmap, norm=norm, aspect='auto'); ax.grid(False); ax.tick_params(length=0)
    ax.set_xticks(range(df.shape[1]), df.columns, fontsize=fs, rotation=rot, ha='right' if rot else 'center'); ax.set_yticks(range(df.shape[0]), df.index, fontsize=fs)
    for s in ax.spines.values(): s.set_visible(False)
    for i in range(v.shape[0]):
        for j in range(v.shape[1]):
            if not np.isnan(v[i, j]): ax.text(j, i, fmt.format(v[i, j]), ha='center', va='center', fontsize=fs - 1.5, color='white' if norm(v[i, j]) > .55 else INK)
def a5(a): return '60대+' if a >= 60 else (f'{a // 10 * 10}대' if a >= 20 else None)
""")

# ------------------------------------------------------------------ 1
md(r"""
## 1. 분석 단위와 정답(타깃) 정의
""")
code(r"""
%%time
# --- 코드 대응 (03에서 만든 강남·춘천 대응표 + 서울 전체 경계 코드)
CODES = pd.read_csv(EXT / 'codes/읍면동_코드대응표_강남_춘천.csv', dtype=str)
NAME = dict(zip(CODES['SGIS코드'], CODES['읍면동명']))
gj = json.load(open(EXT / 'codes/HangJeongDong_ver20250701.geojson', encoding='utf-8'))
cd = pd.DataFrame([f['properties'] for f in gj['features']])
seoul_cd = cd[(cd['sido'] == '11') & (cd['adm_cd'] != '11230511')].assign(k7=lambda d: d['adm_cd'].str[:7])

# --- 정답: 서울 관심집단 6개월 평균 (동 × 성 × 연령5)
L = []
for m in range(7, 13):
    d = pd.read_excel(EXT / f'seoul_interest/interest_2025_{m:02d}.xlsx'); L.append(d.loc[:, ~d.columns.astype(str).str.startswith('Unnamed')].assign(기준년월=f'2025{m:02d}'))
si = pd.concat(L)
si['연령5'] = si['연령대'].map(a5); si['성별'] = si['성별'].map({1: '남성', 2: '여성'})
GI, GC = '외출-커뮤니케이션이 모두 적은 집단(전체)', '커뮤니케이션이 적은 집단'
TS = si.groupby(['행정동코드', '자치구', '행정동명', '성별', '연령5'])[['총인구', '1인가구수', GI, GC]].sum().reset_index()
TS['k7'] = TS['행정동코드'].astype(str)
TS = TS.merge(seoul_cd[['k7', 'adm_cd', 'adm_cd2']], on='k7', how='inner')
broken = TS.groupby('k7')[GC].sum(); broken = broken[broken == 0].index           # 1인가구 기반 집단이 전부 0인 결함 동
TS = TS[~TS['k7'].isin(broken)].copy()
TS['y'] = TS[GI] / TS['총인구'] * 100                                              # 주 타깃: 외출·소통 모두 적음 (인구 %)
TS['y_rel'] = TS['y'] / TS.groupby(['성별', '연령5'])['y'].transform('mean')      # 같은 성·연령 서울 평균 대비
TS['cell'] = TS['성별'] + '_' + TS['연령5']
print(f"서울 정답: {TS['k7'].nunique()}개 동 × 성 2 × 연령 5 = {len(TS):,}행 | 결함 제외 동: {list(TS.loc[TS['k7'].isin(broken), '행정동명'].unique()) or list(broken)}")
display(TS.groupby('연령5')['y'].describe()[['mean', '50%', 'std']].T.round(2))
""")
md(r"""
⚠ 정답 지표는 매뉴얼상 **연령대별 상위 2%**로 정의돼 있어, 연령 간 평균 차이가 설계상 거의 없습니다(위 표). 그래서 이 정답으로는 **같은 성·연령 안에서 동끼리의 차이**만 검증할 수 있습니다. 연령별 위험 수준은 **공식 고립률(B)**로 정합니다.
""")

# ------------------------------------------------------------------ 2
md("## 2. 기본 위험 B — 공식 사회적 고립도 (성 · 연령)")
code(r"""
AGE_RATE = {'20대': 24.4, '30대': 26.5, '40대': 29.5, '50대': 37.2, '60대+': 39.4}     # 통계청 사회조사 2025 (19~29세를 20대로)
SEX_MULT = {'남성': 35.7 / 33.0, '여성': 30.5 / 33.0}                                 # 남 35.7%, 여 30.5%, 전체 33.0%
B = pd.DataFrame([(s, a, AGE_RATE[a] * SEX_MULT[s]) for s in SEX for a in AGE5], columns=['성별', '연령5', 'B 기본 고립률(%)'])
B.pivot(index='연령5', columns='성별', values='B 기본 고립률(%)').reindex(AGE5).round(1)
""")

# ------------------------------------------------------------------ 3
md("## 3. 구조 위험 S — 서울 전체 동으로 학습 (외부: 주민등록)")
code(r"""
pop = pd.read_csv(EXT / 'mois/행정동_성별연령별_주민등록인구_20260930.csv', encoding='cp949')
hh = pd.read_csv(EXT / 'mois/행정동_세대원수별_세대수_20260930.csv', encoding='cp949')
pop['c'] = pop['행정기관코드'].astype(str); hh['c'] = hh['행정기관코드'].astype(str)
ac = {c: (int(re.match(r'(\d+)', c).group(1)), '남성' if '남자' in c else '여성') for c in pop.columns if re.match(r'^\d+세', c)}
lg = pop.melt(id_vars=['c'], value_vars=list(ac), var_name='col', value_name='n')
lg['나이'] = lg['col'].map(lambda c: ac[c][0]); lg['성별'] = lg['col'].map(lambda c: ac[c][1]); lg['연령5'] = lg['나이'].map(a5)
tot = lg.groupby('c')['n'].sum()
STRUCT = pd.DataFrame({'1인세대비율': hh.set_index('c')['1인세대'] / hh.set_index('c')['전체세대수'] * 100,
                       '65세+비율': lg[lg['나이'] >= 65].groupby('c')['n'].sum() / tot * 100,
                       '20~39세비율': lg[lg['나이'].between(20, 39)].groupby('c')['n'].sum() / tot * 100,
                       '40~64세 남성비율': lg[lg['나이'].between(40, 64) & (lg['성별'] == '남성')].groupby('c')['n'].sum() / tot * 100,
                       'log 주민인구': np.log(tot)})
RESID = lg.dropna(subset=['연령5']).groupby(['c', '성별', '연령5'])['n'].sum().rename('주민')     # 동 × 성 × 연령5 거주 인구
F_S = list(STRUCT.columns)
TS = TS.merge(STRUCT, left_on='adm_cd2', right_index=True, how='inner')
cell_d = pd.get_dummies(TS['cell'], dtype=float)
XS = pd.concat([cell_d, TS[F_S]], axis=1)
ridge = lambda: make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 3, 20)))
predS = np.zeros(len(TS))
for tr, te in GroupKFold(25).split(XS, TS['y_rel'], TS['자치구']):                 # 자치구 단위로 빼고 검증 (공간 누수 방지)
    predS[te] = ridge().fit(XS.iloc[tr], TS['y_rel'].iloc[tr]).predict(XS.iloc[te])
TS['S_cv'] = predS
def within_rho(df, p, y):
    return np.mean([spearmanr(g[y], g[p])[0] for _, g in df.groupby('cell') if g[p].nunique() > 1])
r2 = 1 - ((TS['y_rel'] - predS) ** 2).sum() / ((TS['y_rel'] - TS['y_rel'].mean()) ** 2).sum()
print(f"서울 전체(자치구 교차검증): R² {r2:.3f} | 같은 성·연령 안 동 순위 ρ {within_rho(TS, 'S_cv', 'y_rel'):.3f}")
gn = TS[TS['자치구'] == '강남구']
print(f"그중 강남구 동: 동 순위 ρ {within_rho(gn, 'S_cv', 'y_rel'):.3f}")
S_MODEL = ridge().fit(XS, TS['y_rel'])
coefS = pd.Series(S_MODEL[-1].coef_[-len(F_S):], index=F_S)
display(coefS.round(3).to_frame('표준화 계수 (+ = 고립 지표↑)'))
""")

# ------------------------------------------------------------------ 4
md("## 4. 행동 위험 A — SK 유동인구 (제공 데이터)")
code(r"""
%%time
age = pd.read_csv(DATA / 'SK_유동인구_성연령.csv', dtype={'기준년월': str, '소지역코드': str})
age['SGIS코드'] = age['소지역코드'].str[:8]; age = age[age['SGIS코드'].isin(CODES['SGIS코드'])]
MC = [c for c in age.columns if c.startswith('남자')]; FC = [c for c in age.columns if c.startswith('여자')]
SKA = ['10대', '20대', '30대', '40대', '50대', '60대+']
skl = age.groupby(['SGIS코드', '기준년월'])[MC + FC].sum().stack().rename('유동').reset_index()
skl['성별'] = np.where(skl['level_2'].str.startswith('남자'), '남성', '여성'); skl['연령5'] = skl['level_2'].map(dict(zip(MC + FC, SKA * 2)))
skl = skl[skl['연령5'] != '10대']
tm = pd.read_csv(DATA / 'SK_유동인구_시간대.csv', dtype={'기준년월': str, '소지역코드': str})
tm['SGIS코드'] = tm['소지역코드'].str[:8]; tm = tm[tm['SGIS코드'].isin(CODES['SGIS코드'])]
TM = [f'유동인구수_{h:02d}시간대' for h in range(24)]
wk = pd.read_csv(DATA / 'SK_유동인구_요일.csv', dtype={'기준년월': str, '소지역코드': str})
wk['SGIS코드'] = wk['소지역코드'].str[:8]; wk = wk[wk['SGIS코드'].isin(CODES['SGIS코드'])]
WD = [f'{d}요일 유동인구' for d in '월화수목금토일']

# 분석 단위 프레임: 강남·춘천 동 × 성 × 연령5
U = (skl.groupby(['SGIS코드', '성별', '연령5'])['유동'].mean().rename('집단유동').reset_index())
U = U.merge(CODES[['SGIS코드', '행정기관코드', '지역', '유형']], on='SGIS코드')
U['읍면동명'] = U['SGIS코드'].map(NAME)
U = U.merge(RESID.reset_index().rename(columns={'c': '행정기관코드'}), on=['행정기관코드', '성별', '연령5'], how='left')
dong_f = skl.groupby('SGIS코드')['유동'].sum() / 6
seas = skl.groupby(['SGIS코드', '성별', '연령5', '기준년월'])['유동'].sum().unstack()
tmS = tm.groupby('SGIS코드')[TM].sum(); wkS = wk.groupby('SGIS코드')[WD].sum()
dong_res = U.groupby('SGIS코드')['주민'].sum()
U['유동/거주 비'] = np.log((U['집단유동'] + 1) / (U['주민'] + 1))                                  # 집단이 동네에 머무는 정도
U['집단 유동 비중'] = U['집단유동'] / U['SGIS코드'].map(dong_f)
U['야간 유동/주민'] = U['SGIS코드'].map(np.log((tmS[TM[:6]].sum(axis=1) / 6 / 6 + 1) / (dong_res + 1)))
U['주간/야간'] = U['SGIS코드'].map((tmS[TM[9:18]].sum(axis=1) / 9) / (tmS[TM[:6]].sum(axis=1) / 6))
U['주말/평일'] = U['SGIS코드'].map(wkS[WD[5:]].mean(axis=1) / wkS[WD[:5]].mean(axis=1))
U['겨울 변화'] = U.set_index(['SGIS코드', '성별', '연령5']).index.map(
    (seas['202512'] / seas[['202507', '202508']].mean(axis=1) - 1))
F_A = ['유동/거주 비', '집단 유동 비중', '야간 유동/주민', '주간/야간', '주말/평일', '겨울 변화']
# 지역마다 규모가 다르므로(03-6 외삽 문제) 지역 안에서 같은 성·연령끼리의 백분위 순위로 변환
for f in F_A:
    U[f + '_p'] = U.groupby(['지역', '성별', '연령5'])[f].rank(pct=True)
FA_P = [f + '_p' for f in F_A]
U['cell'] = U['성별'] + '_' + U['연령5']
# 구조 위험 S: 서울 전체 모델을 강남·춘천에 적용
US = U.merge(STRUCT, left_on='행정기관코드', right_index=True, how='left')
U['S'] = S_MODEL.predict(pd.concat([pd.get_dummies(US['cell'], dtype=float).reindex(columns=cell_d.columns, fill_value=0), US[F_S]], axis=1))
U = U.merge(B, on=['성별', '연령5'])
print('분석 단위:', U.groupby('지역').size().to_dict(), '| 결측:', int(U[F_A + ['S']].isna().sum().sum()))
U.groupby('지역')[F_A].median().round(3)
""")
md(r"""
### 4-1. 학습 — 강남 정답으로 '구조(S)'와 'SK 행동 지표'의 비중을 함께 학습 (동 하나씩 빼고 검증)
정답의 '같은 성·연령 대비 상대값(log y_rel)'을 S와 SK 지표로 설명합니다. S를 고정된 출발점이 아니라 **변수 하나로** 넣어서, 외부 구조를 얼마나 믿을지 모델이 스스로 정하게 합니다.
""")
code(r"""
G = U[U['지역'] == '서울 강남구'].merge(TS[['adm_cd', '성별', '연령5', 'y', 'y_rel', '총인구']].rename(columns={'adm_cd': 'SGIS코드'}),
                                    on=['SGIS코드', '성별', '연령5'], how='inner')
U['logS'] = np.log(U['S'].clip(lower=.2)); G['logS'] = np.log(G['S'].clip(lower=.2))
F_ALL = FA_P + ['logS']
print('강남 학습 행:', len(G), '| 동:', G['SGIS코드'].nunique())
ridge = lambda: make_pipeline(StandardScaler(), RidgeCV(alphas=np.logspace(-2, 3, 20)))
def lodo(df, feats):                       # 동 하나씩 빼고 학습 → 빠진 동 예측
    p = pd.Series(0.0, index=df.index)
    for tr, te in LeaveOneGroupOut().split(df, groups=df['SGIS코드']):
        p.iloc[te] = ridge().fit(df.iloc[tr][feats], np.log(df.iloc[tr]['y_rel'])).predict(df.iloc[te][feats])
    return np.exp(p)
G['SA_cv'] = lodo(G, F_ALL); G['S_only_cv'] = lodo(G, ['logS']); G['A_only_cv'] = lodo(G, FA_P)
SA_MODEL = ridge().fit(G[F_ALL], np.log(G['y_rel']))
coefA = pd.Series(SA_MODEL[-1].coef_, index=F_A + ['구조 S (주민등록)'])
fig, ax = plt.subplots(figsize=(7, 3.4))
ax.barh(coefA.index, coefA.values, color=['#eb6834' if v > 0 else '#2a78d6' for v in coefA.values]); ax.axvline(0, color=INK2, lw=.8)
ax.set_title('구조 + SK 결합 모델의 표준화 계수 — 주황 = 높을수록 고립 지표↑'); plt.show()
U['SA'] = np.exp(SA_MODEL.predict(U[F_ALL]))       # 구조 + SK 행동을 합친 상대 위험 (1 = 같은 성·연령 평균)
U.groupby('지역')['SA'].describe()[['mean', 'min', '50%', 'max']].round(3)
""")

# ------------------------------------------------------------------ 5
md("## 5. 집단 행동 C — 신한카드 (제공 데이터)")
code(r"""
%%time
REN = {'기준일자': '일자', '가맹점 위치정보(시·군·구)': '지역', '가맹점 업종': '업종', '이용고객 성별': '성별', '이용고객 연령대': '연령대',
       '결제금액 합계': '금액', '결제건수 합계': '건수'}
EXCL = ['ZZ_나머지', '세금공과금', '보험', '상품권/복권', '통신요금(이동,시내전화)', '통신요금(PC통신,무선호출)', '학교등록금', '결제대행(PG)']
OUTD_ = ['한식', '양식', '일식', '중식', '기타요식', '패스트푸드', '제과점', '커피전문점', '노래방', '영화/공연', '게임방/오락실', '종합레저타운/놀이동산',
         '스포츠시설', '헬스장', '실내/실외골프장', '싸우나/목욕탕', '유흥업소', '주류판매', '여행사/항공사', '호텔/콘도', '고속버스/철도/여객선', '택시']
MED = ['일반병원', '종합병원', '치과병원', '한의원', '기타의료', '약국', '보건소']
card = pd.read_csv(DATA / '신한카드_데이터1_시간대별.csv', dtype={'기준일자': str}).rename(columns=REN)
card = card[(card['성별'] != '법인') & ~card['업종'].isin(EXCL)].copy()
card['일자'] = pd.to_datetime(card['일자'])
card['연령5'] = card['연령대'].str.replace(' ', '').map(lambda a: None if a == '10대' else ('60대+' if a in ('60대', '70대', '80대', '90대') else a))
card = card.dropna(subset=['연령5'])
k = ['지역', '성별', '연령5']; base = card.groupby(k)['건수'].sum()
CI = pd.DataFrame({'외부활동%': card[card['업종'].isin(OUTD_)].groupby(k)['건수'].sum() / base * 100,
                   '의료%': card[card['업종'].isin(MED)].groupby(k)['건수'].sum() / base * 100,
                   '심야%': card[card['시간대'] == '00_05'].groupby(k)['건수'].sum() / base * 100})
pop_sh = skl.merge(CODES[['SGIS코드', '지역']], on='SGIS코드').groupby(k)['유동'].sum(); pop_sh = pop_sh / pop_sh.groupby('지역').transform('sum')
CI['참여도'] = (base / base.groupby('지역').transform('sum')) / pop_sh
# 같은 연령 안(성별 2 × 지역 2 = 4개 집단)에서의 상대 위치 → 연령 효과는 B가 이미 반영하므로 중복을 피함
Z = CI.groupby('연령5').transform(lambda s: (s - s.mean()) / (s.std() + 1e-9))
# 방향은 02 결과·문헌 기반, 크기는 정답으로 검증할 수 없어 보수적으로(±10% 안팎) 둠
W_C = {'외부활동%': -.04, '의료%': +.02, '참여도': -.04, '심야%': +.02}
CI['C'] = np.exp(sum(w * Z[f] for f, w in W_C.items()))
display(CI.round(3))
U = U.merge(CI[['C']].reset_index(), on=k, how='left')
""")

# ------------------------------------------------------------------ 6
md("## 6. 위험도 산출 · 5단계")
code(r"""
U['위험점수'] = U['B 기본 고립률(%)'] * U['SA'] * U['C']
# 5단계: 거주 인구 가중 누적 비중으로 구분 — '주의' 이상 = 공식 사회적 고립도 33.0%에 맞춤 (유병률 기준점)
CUTS = [('심각', .05), ('경계', .15), ('주의', .33), ('관심', .60), ('정상', 1.0)]
def levels(df, score='위험점수', w='주민'):
    d = df.sort_values(score, ascending=False); cum = d[w].fillna(0).cumsum() / d[w].fillna(0).sum()
    lab = pd.Series('정상', index=d.index)
    for name, cut in reversed(CUTS): lab[cum <= cut] = name
    return lab.reindex(df.index)
# ① 지역 내 단계: 대응은 구청·시청이 각자 관할에서 하고, SK 지표를 지역 안 순위로 썼으므로 지역끼리 한 줄로 세우지 않음
U['단계'] = pd.concat([levels(g) for _, g in U.groupby('지역')])
# ② 지역 × 생애단계 내 단계: 생애주기별 사업 대상 선정용 (경남 지침 p.7 '생애주기별로 사업대상을 선정' 권고)
U['생애단계 내 단계'] = pd.concat([levels(g) for _, g in U.groupby(['지역', '연령5'])])
ORDER = ['심각', '경계', '주의', '관심', '정상']
summ = U.groupby(['지역', '단계'])['주민'].sum().unstack().reindex(columns=ORDER).fillna(0)
summ = (summ.div(summ.sum(axis=1), axis=0) * 100).round(1)
print('① 지역 내 단계 — 지역별 거주 인구 분포(%) (정의상 각 지역 5/10/18/27/40%)'); display(summ)
lv = U.groupby(['지역', '연령5', '단계'])['주민'].sum().unstack().reindex(columns=ORDER).fillna(0)
print('① 지역 내 단계 — 지역 × 연령별 거주 인구 분포(%)'); display((lv.div(lv.sum(axis=1), axis=0) * 100).round(1))
lv2 = U.groupby(['지역', '연령5', '생애단계 내 단계'])['주민'].sum().unstack().reindex(columns=ORDER).fillna(0)
print('② 생애단계 내 단계 — 지역 × 연령별 거주 인구 분포(%)'); display((lv2.div(lv2.sum(axis=1), axis=0) * 100).round(1))
fig, axes = plt.subplots(1, 2, figsize=(14, 7.5))
for ax, r in zip(axes, MAIN):
    p = U[U['지역'] == r].pivot_table(index='읍면동명', columns=['성별', '연령5'], values='위험점수')
    p = p.loc[p.mean(axis=1).sort_values(ascending=False).index]; p.columns = [f'{s[0]}{a}' for s, a in p.columns]
    heat(ax, p, fmt='{:.0f}', vmin=U['위험점수'].quantile(.02), vmax=U['위험점수'].quantile(.98), fs=7, rot=45); ax.set_title(f'{r} — 위험점수 (남·여 × 연령)')
plt.tight_layout(); plt.show()
""")
code(r"""
cols = ['지역', '읍면동명', '성별', '연령5', '주민', '위험점수', '단계', '생애단계 내 단계', 'B 기본 고립률(%)', 'S', 'SA', 'C']
for r in MAIN:
    print(f'① [{r}] 지역 내 우선 지원 대상 상위 8'); display(U[U['지역'] == r].sort_values('위험점수', ascending=False)[cols].head(8).round(2))
print('② 지역 × 생애단계별 우선 지원 대상 1순위')
display(U.sort_values('위험점수', ascending=False).groupby(['지역', '연령5'], sort=False).head(1).sort_values(['지역', '연령5'])[cols].round(2))
for r in MAIN:
    print(f'[{r}] 단계별 집단 수:', U[U['지역'] == r]['단계'].value_counts().reindex(ORDER).fillna(0).astype(int).to_dict())
""")

# ------------------------------------------------------------------ 7
md(r"""
## 7. 성능 평가 — 제공 데이터를 빼면 얼마나 떨어지는가 (제거 실험)
- **동 순위 ρ**: 같은 성·연령 안에서 21개 동의 순위를 정답과 얼마나 같게 매기는가 (0 = 무작위, 1 = 완벽)
- **향상도**: 점수 상위 20% 동(4~5개)의 실제 고립 비율이 그 성·연령 평균의 몇 배인가 (1.0 = 무작위). 정답으로 고른 경우(이론상 최대)도 함께 표시
""")
code(r"""
E = G.merge(U[['SGIS코드', '성별', '연령5', 'C']], on=['SGIS코드', '성별', '연령5'])
E['B'] = E['B 기본 고립률(%)']
VAR = [('B (공식 성·연령만)', E['B'], '외부'), ('B × S (외부 구조만)', E['B'] * E['S_only_cv'], '외부'),
       ('B × A (SK만)', E['B'] * E['A_only_cv'], '제공 (SK)'), ('B × S·A (외부 + SK)', E['B'] * E['SA_cv'], '외부 + 제공 (SK)'),
       ('B × S·A × C (최종)', E['B'] * E['SA_cv'] * E['C'], '외부 + 제공 (SK·신한)'), ('정답으로 고른 경우 (이론상 최대)', E['y'], '—')]
def lift(df, s, share=.2):
    out = []
    for _, g in df.assign(_s=s).groupby('cell'):
        n = max(1, int(round(len(g) * share)))
        out.append(1.0 if g['_s'].nunique() == 1 else g.nlargest(n, '_s')['y'].mean() / g['y'].mean())   # 점수가 모두 같으면 무작위(1.0)
    return np.mean(out)
rows = []
for name, s, src in VAR:
    wr = np.array([spearmanr(g['y'], g['_s'])[0] if g['_s'].nunique() > 1 else 0 for _, g in E.assign(_s=s).groupby('cell')])
    bs = [np.random.default_rng(i).choice(wr, len(wr)).mean() for i in range(1000)]
    rows.append({'모델': name, '사용 데이터': src, '동 순위 ρ': wr.mean(), '95% 구간': f'{np.percentile(bs, 2.5):.2f}~{np.percentile(bs, 97.5):.2f}',
                 '향상도 (상위 20% 동)': lift(E, s)})
ABL = pd.DataFrame(rows)
ABL.round(3)
""")
code(r"""
fig, axes = plt.subplots(1, 2, figsize=(13, 3.6), sharey=True)
A_ = ABL.iloc[:-1][::-1]; colors = [GRAY, '#b7d3f6', '#2a78d6', '#1c5cab', '#0d366b'][::-1]
axes[0].barh(A_['모델'], A_['동 순위 ρ'], color=colors, height=.6); axes[0].set_title('같은 성·연령 안 동 순위 ρ (동 하나씩 빼고 검증)')
axes[1].barh(A_['모델'], A_['향상도 (상위 20% 동)'], color=colors, height=.6); axes[1].axvline(1, color=INK2, lw=.8)
axes[1].axvline(ABL.iloc[-1]['향상도 (상위 20% 동)'], color='#c43a39', lw=1); axes[1].set_xlim(.8, None)
axes[1].text(ABL.iloc[-1]['향상도 (상위 20% 동)'], 4.4, ' 이론상 최대', color='#c43a39', fontsize=8.5); axes[1].set_title('향상도 — 상위 20% 동의 고립 비율 / 평균')
for ax, col in zip(axes, ['동 순위 ρ', '향상도 (상위 20% 동)']):
    for y_, v in enumerate(A_[col]): ax.text(v, y_, f' {v:.2f}', va='center', fontsize=8.5, color=INK2)
plt.tight_layout(); plt.show()
""")

# ------------------------------------------------------------------ 8
md("## 8. 요인 기여도 — 왜 이 동·집단이 위험한가 (에이전트 '원인 분석'의 근거)")
code(r"""
sc, mu, cf = SA_MODEL[0].scale_, SA_MODEL[0].mean_, SA_MODEL[-1].coef_
contrib = pd.DataFrame({('SK: ' + f if f != 'logS' else '구조(주민등록)'): cf[i] * (U[fp] - mu[i]) / sc[i]
                        for i, (f, fp) in enumerate(zip(F_A + ['logS'], F_ALL))})
contrib['기본(성·연령)'] = np.log(U['B 기본 고립률(%)'] / U['B 기본 고립률(%)'].mean())
contrib['카드(집단 행동)'] = np.log(U['C'])
U['logSK'] = contrib[[c for c in contrib if c.startswith('SK')]].sum(axis=1); U['logST'] = contrib['구조(주민등록)']
U['logB'] = np.log(U['B 기본 고립률(%)']); U['logC'] = np.log(U['C'])
U['주요 요인'] = [', '.join(f"{c} {'↑' if contrib.loc[i, c] > 0 else '↓'}" for c in contrib.loc[i].abs().sort_values(ascending=False).index[:3]) for i in U.index]
share = contrib.abs().div(contrib.abs().sum(axis=1), axis=0).mean().sort_values(ascending=False) * 100
fig, ax = plt.subplots(figsize=(7.5, 3.8))
ax.barh(share.index[::-1], share.values[::-1], color=['#2a78d6' if s.startswith(('SK', '카드')) else GRAY for s in share.index[::-1]])
ax.set_title('위험점수 차이에 대한 평균 기여 비중 (파랑 = 제공 데이터)'); ax.xaxis.set_major_formatter(mtick.PercentFormatter(decimals=0)); plt.show()
print(f"제공 데이터(SK·신한) 기여 합계: {share[[s for s in share.index if s.startswith(('SK', '카드'))]].sum():.1f}%  "
      f"(SK {share[[s for s in share.index if s.startswith('SK')]].sum():.1f}%, 카드 {share.get('카드(집단 행동)', 0):.1f}%)")
U.sort_values('위험점수', ascending=False)[['지역', '읍면동명', '성별', '연령5', '단계', '주요 요인']].head(10)
""")

# ------------------------------------------------------------------ 9
md(r"""
## 9. 시점 신호 T — 신한카드 일별 + 기상청 보정 → '지속 감소' 시 단계 상향
- 대상: 지역 × 성 × 연령(20개 집단)의 **외부·사회활동형 소비** 일별 건수
- 기준선: 요일·공휴일·추석·월 + **날씨**(기상청 ASOS)로 예측한 '평소 값' → 실제와의 차이(잔차)
- **상향 규칙**: 잔차의 7일 이동평균이 −10% 이하로 **7일 이상 지속** → 해당 집단의 단계를 한 칸 올림 (일시적 감소는 무시)
""")
code(r"""
%%time
W = pd.read_csv(EXT / 'weather/kma_asos_daily_서울_춘천_202507_202512.csv', parse_dates=['일자'])[['지역', '일자', '평균기온', '최고기온', '일강수량']]
W['일강수량'] = W['일강수량'].fillna(0)
HOL = pd.to_datetime(['2025-08-15', '2025-10-03', '2025-10-06', '2025-10-07', '2025-10-08', '2025-10-09', '2025-12-25'])
CH = pd.date_range('2025-10-03', '2025-10-09')
def design(d, weather=True):
    X = pd.DataFrame({'const': 1.0}, index=d.index); dow = d['일자'].dt.dayofweek
    for kk in range(1, 7): X[f'd{kk}'] = (dow == kk) * 1.0
    X['hol'] = d['일자'].isin(HOL) * 1.0; X['ch'] = d['일자'].isin(CH) * 1.0
    X['x24'] = (d['일자'] == '2025-12-24') * 1.0; X['x31'] = (d['일자'] == '2025-12-31') * 1.0
    for kk in range(8, 13): X[f'm{kk}'] = (d['일자'].dt.month == kk) * 1.0
    if weather:
        p = d['일강수량']; X['r1'] = ((p > .5) & (p <= 10)) * 1.0; X['r2'] = ((p > 10) & (p <= 30)) * 1.0; X['r3'] = (p > 30) * 1.0
        X['td'] = d['평균기온'] - d.groupby(d['일자'].dt.month)['평균기온'].transform('mean'); X['heat'] = (d['최고기온'] >= 33) * 1.0
    return X
def resid(d, y, weather=True, train_mask=None):
    X = design(d, weather); m = np.ones(len(y), bool) if train_mask is None else train_mask
    b, *_ = np.linalg.lstsq(X.values[m], y[m], rcond=None); return pd.Series(y - X.values @ b, index=d['일자'])
def first_alarm(r, drop, days):
    roll = r.rolling(7).mean().values; n = 0
    for i, v in enumerate(roll):
        n = n + 1 if (not np.isnan(v) and v < np.log(1 + drop)) else 0
        if n >= days: return i
    return None
def count_episodes(r, drop, days):
    roll = r.rolling(7).mean().values; runs, n = 0, 0
    for v in list(roll) + [np.nan]:
        if not np.isnan(v) and v < np.log(1 + drop): n += 1
        else:
            runs += n >= days; n = 0
    return runs
RULES = {'기본 (−10%, 7일 지속)': (-.10, 7), '민감 (−7%, 5일 지속)': (-.07, 5)}
SER = {}
for (reg, sx, ag), g in card[card['업종'].isin(OUTD_)].groupby(['지역', '성별', '연령5']):
    SER[(reg, sx, ag)] = g.groupby('일자')['건수'].sum().rename('y').reset_index().merge(W[W['지역'] == reg], on='일자')
rows = []
for rname, (drop, days) in RULES.items():
    for wf, tag in [(False, '달력만 보정'), (True, '달력 + 날씨 보정')]:
        rows.append({'규칙': rname, '보정': tag, '6개월간 오경보 구간 수 (20개 집단 합)':
                     sum(count_episodes(resid(d, np.log(d['y'].values), wf), drop, days) for d in SER.values())})
FA_TAB = pd.DataFrame(rows); display(FA_TAB)

# 탐지 성능 시험: 실제로 3주간 활동이 줄었다고 가정하고 인위로 넣어 본 뒤, 규칙이 며칠 만에 잡는지 측정
rng = np.random.default_rng(0); sim = []
for D in [-.10, -.15, -.20, -.30]:
    for key, d in SER.items():
        for start in rng.integers(30, 150, 5):
            y = np.log(d['y'].values.copy()); y[start:start + 21] += np.log(1 + D)
            mask = np.ones(len(y), bool); mask[start:start + 28] = False        # 운영처럼: 감소 구간은 '평소 값' 학습에서 제외
            a = first_alarm(resid(d, y, True, mask), -.10, 7)
            hit = a is not None and start <= a < start + 21
            sim.append({'실제 감소폭': f'{int(D * 100)}%', '탐지': hit, '지연(일)': (a - start) if hit else np.nan})
SIM = pd.DataFrame(sim).groupby('실제 감소폭', sort=False).agg(탐지율=('탐지', 'mean'), 평균지연일=('지연(일)', 'mean'))
SIM['탐지율'] = (SIM['탐지율'] * 100).round(1)
print('기본 규칙(−10%, 7일)의 탐지 성능 — 3주간 지속 감소를 넣고, 평소 값은 그 구간을 뺀 데이터로 학습 (20개 집단 × 5회)'); display(SIM.round(1))
fig, axes = plt.subplots(2, 1, figsize=(12, 5.4), sharex=True)
for ax, reg in zip(axes, MAIN):
    for ag, col in zip(AGE5, ['#86b6ef', '#3987e5', '#2a78d6', '#1c5cab', '#0d366b']):
        s = sum(resid(SER[(reg, sx, ag)], np.log(SER[(reg, sx, ag)]['y'].values)).rolling(7).mean() for sx in SEX) / 2
        ax.plot(s.index, (np.exp(s.values) - 1) * 100, color=col, lw=1.2, label=ag)
    ax.axhline(-10, color='#c43a39', lw=1); ax.axhline(0, color=INK2, lw=.6)
    ax.set_title(f'{reg} — 외부활동 소비, 평소 대비 7일 이동평균 편차 (빨간선 = 상향 기준 -10%)'); ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
axes[0].legend(ncol=5, fontsize=8.5); plt.tight_layout(); plt.show()
""")

# ------------------------------------------------------------------ 10
md("## 10. 민감도 — 가중치를 바꿔도 우선순위가 유지되는가")
code(r"""
def score(wA=1., wS=1., wC=1.): return np.exp(U['logB'] + wA * U['logSK'] + wS * U['logST'] + wC * U['logC'])
base_rank = U['위험점수'].rank(ascending=False); base_top = set(U.nlargest(int(len(U) * .15), '위험점수').index)
rows = []
for nm, kw in [('SK 가중 ×0.5', dict(wA=.5)), ('SK 가중 ×1.5', dict(wA=1.5)), ('구조 가중 ×0.5', dict(wS=.5)), ('구조 가중 ×1.5', dict(wS=1.5)),
               ('카드 가중 ×0', dict(wC=0)), ('카드 가중 ×2', dict(wC=2))]:
    s = score(**kw); top = set(s.nlargest(int(len(U) * .15)).index)
    rows.append({'변경': nm, '전체 순위 상관 ρ': spearmanr(base_rank, s.rank(ascending=False))[0],
                 '상위 15% 집단 유지율(%)': len(top & base_top) / len(base_top) * 100,
                 '단계가 바뀐 집단(%)': (levels(U.assign(_s=s), '_s') != U['단계']).mean() * 100})
pd.DataFrame(rows).round(3)
""")
code(r"""
SAVE = ['지역', '읍면동명', 'SGIS코드', '행정기관코드', '유형', '성별', '연령5', '주민', 'B 기본 고립률(%)', 'S', 'SA', 'C', '위험점수', '단계', '생애단계 내 단계', '주요 요인'] + F_A
U[SAVE].sort_values('위험점수', ascending=False).to_csv(OUTD / '위험도_강남_춘천_동x성x연령.csv', index=False, encoding='utf-8-sig')
SIM.to_csv(OUTD / '시점신호_탐지성능.csv', encoding='utf-8-sig')
print('저장: outputs/위험도_강남_춘천_동x성x연령.csv, outputs/시점신호_탐지성능.csv')
""")

# ------------------------------------------------------------------ 11 (견고성)
md(r"""
## 11. 견고성 점검 — 설계 동결 · 시간 분할 검증 · 모델 비교 · 성별 분리 단계
04의 설계는 결과를 보면서 몇 차례 고쳤기 때문에, 같은 데이터로 평가하면 성능이 낙관적일 수 있습니다. 그래서 **설계를 고정(동결)한 뒤**, 학습에 쓰지 않은 **미래 기간**으로 다시 평가합니다.
""")
code(r"""
CONFIG = {'동결일': '2026-10-13', '기본위험 B(연령별 %)': AGE_RATE, '성별 배수': SEX_MULT, '구조 지표 S': F_S, 'SK 행동 지표 A': F_A,
          '카드 가중치 C': W_C, '5단계 기준(누적 인구 비중)': CUTS, '실시간 규칙 T': {'7일 평균 감소': -0.10, '지속 일수': 7},
          '모델': 'StandardScaler + RidgeCV(alphas=logspace(-2,3,20))', '정답': GI + ' / 총인구, 같은 성·연령 서울 평균 대비'}
json.dump(CONFIG, open(OUTD / '모델설정_동결.json', 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
print('설계 동결: outputs/모델설정_동결.json'); pd.Series({k: str(v)[:90] for k, v in CONFIG.items()}).to_frame('값')
""")
md(r"""
### 11-1. 시간 분할 검증 — 7~9월로 학습하고 10~12월을 맞히는가
- SK 지표와 정답을 기간별로 다시 만듭니다. '겨울 변화'는 3개월 안에서 정의할 수 없어 **기간 내 변화(마지막 달 ÷ 첫 달)**로 바꿉니다.
- (a) 같은 기간 안에서 동 하나씩 빼고 검증 → (b) 과거로 학습해 미래를 예측(같은 동) → (c) **과거 + 다른 동으로 학습해 미래의 처음 보는 동을 예측**(가장 엄격)
- 비교 기준: '지난 기간 정답을 그대로 다음 기간 예측으로 쓰기'(정답 자체의 시간 안정성)
""")
code(r"""
%%time
P1, P2 = ['202507', '202508', '202509'], ['202510', '202511', '202512']
K3 = ['SGIS코드', '성별', '연령5']
def period_target(months):
    s_ = si[si['기준년월'].isin(months)].groupby(['행정동코드', '자치구', '성별', '연령5'])[['총인구', GI]].sum().reset_index()
    s_['y'] = s_[GI] / s_['총인구'] * 100
    s_['y_rel'] = s_['y'] / s_.groupby(['성별', '연령5'])['y'].transform('mean')
    s_ = s_[s_['자치구'] == '강남구'].copy(); s_['SGIS코드'] = s_['행정동코드'].astype(str) + '0'
    return s_[s_['SGIS코드'] != '11230740'][K3 + ['y', 'y_rel']]
def period_features(months):
    sk = skl[skl['기준년월'].isin(months)]
    g = sk.groupby(K3)['유동'].mean().rename('집단유동').reset_index().merge(U[K3 + ['지역', '주민', 'logS', 'cell']], on=K3)
    df_ = sk.groupby('SGIS코드')['유동'].sum() / len(months)
    t_ = tm[tm['기준년월'].isin(months)].groupby('SGIS코드')[TM].sum(); w_ = wk[wk['기준년월'].isin(months)].groupby('SGIS코드')[WD].sum()
    dres = U.groupby('SGIS코드')['주민'].sum()
    g['유동/거주 비'] = np.log((g['집단유동'] + 1) / (g['주민'] + 1))
    g['집단 유동 비중'] = g['집단유동'] / g['SGIS코드'].map(df_)
    g['야간 유동/주민'] = g['SGIS코드'].map(np.log((t_[TM[:6]].sum(axis=1) / 6 / len(months) + 1) / (dres + 1)))
    g['주간/야간'] = g['SGIS코드'].map((t_[TM[9:18]].sum(axis=1) / 9) / (t_[TM[:6]].sum(axis=1) / 6))
    g['주말/평일'] = g['SGIS코드'].map(w_[WD[5:]].mean(axis=1) / w_[WD[:5]].mean(axis=1))
    se = sk.groupby(K3 + ['기준년월'])['유동'].sum().unstack()
    g['겨울 변화'] = g.set_index(K3).index.map(se[months[-1]] / se[months[0]] - 1)     # 기간 내 변화
    for f in F_A: g[f + '_p'] = g.groupby(['지역', '성별', '연령5'])[f].rank(pct=True)
    return g
D1 = period_features(P1).merge(period_target(P1), on=K3); D2 = period_features(P2).merge(period_target(P2), on=K3)
D2 = D2.set_index(K3).loc[D1.set_index(K3).index].reset_index()         # 같은 순서로 정렬
print('기간별 학습 행:', len(D1), len(D2))
def rho_cells(df, pred):
    wr = np.array([spearmanr(g['y_rel'], g['_p'])[0] for _, g in df.assign(_p=pred).groupby('cell')])
    bs = [np.random.default_rng(i).choice(wr, len(wr)).mean() for i in range(1000)]
    return wr.mean(), f'{np.percentile(bs, 2.5):.2f}~{np.percentile(bs, 97.5):.2f}'
rows = []
p = np.zeros(len(D1))
for tr, te in LeaveOneGroupOut().split(D1, groups=D1['SGIS코드']):
    p[te] = ridge().fit(D1.iloc[tr][F_ALL], np.log(D1.iloc[tr]['y_rel'])).predict(D1.iloc[te][F_ALL])
rows.append(('(a) 7~9월 안에서, 동 하나씩 빼고', *rho_cells(D1, p)))
m = ridge().fit(D1[F_ALL], np.log(D1['y_rel']))
rows.append(('(b) 7~9월로 학습 → 10~12월 예측 (같은 동)', *rho_cells(D2, m.predict(D2[F_ALL]))))
p = np.zeros(len(D2))
for tr, te in LeaveOneGroupOut().split(D1, groups=D1['SGIS코드']):
    p[te] = ridge().fit(D1.iloc[tr][F_ALL], np.log(D1.iloc[tr]['y_rel'])).predict(D2.iloc[te][F_ALL])
rows.append(('(c) 7~9월·다른 동으로 학습 → 10~12월의 처음 보는 동 (가장 엄격)', *rho_cells(D2, p)))
rows.append(('참고: 7~9월 정답을 10~12월 예측으로 그대로 사용', *rho_cells(D2, D1['y_rel'].values)))
TSPLIT = pd.DataFrame(rows, columns=['검증 방식', '동 순위 ρ', '95% 구간'])
TSPLIT.round(3)
""")
md("### 11-2. 모델 비교 — 왜 릿지 회귀인가 (6개월 데이터, 동 하나씩 빼고 검증)")
code(r"""
%%time
from sklearn.linear_model import LassoCV, LinearRegression
from sklearn.ensemble import RandomForestRegressor
import lightgbm as lgb, time
MODELS = {'선형회귀 (규제 없음)': lambda: make_pipeline(StandardScaler(), LinearRegression()),
          '릿지 회귀 (채택)': ridge,
          '라쏘 회귀': lambda: make_pipeline(StandardScaler(), LassoCV(cv=5, random_state=0)),
          '랜덤포레스트': lambda: RandomForestRegressor(n_estimators=300, min_samples_leaf=5, random_state=0, n_jobs=-1),
          'LightGBM': lambda: lgb.LGBMRegressor(n_estimators=300, learning_rate=.03, num_leaves=7, min_child_samples=15, subsample=.8,
                                               subsample_freq=1, colsample_bytree=.8, verbose=-1, random_state=0)}
rows = []
for name, mk in MODELS.items():
    t0 = time.time(); p = np.zeros(len(G)); pt = np.zeros(len(D2))
    for tr, te in LeaveOneGroupOut().split(G, groups=G['SGIS코드']):
        p[te] = mk().fit(G.iloc[tr][F_ALL], np.log(G.iloc[tr]['y_rel'])).predict(G.iloc[te][F_ALL])
    for tr, te in LeaveOneGroupOut().split(D1, groups=D1['SGIS코드']):
        pt[te] = mk().fit(D1.iloc[tr][F_ALL], np.log(D1.iloc[tr]['y_rel'])).predict(D2.iloc[te][F_ALL])
    r1, c1 = rho_cells(G, p); r2, c2 = rho_cells(D2, pt)
    rows.append({'모델': name, '동 순위 ρ (6개월, 동 분할)': r1, '95% 구간': c1, '동 순위 ρ (시간+동 분할)': r2, '95% 구간 ': c2,
                 '요인별 설명 가능': '예' if name in ('선형회귀 (규제 없음)', '릿지 회귀 (채택)', '라쏘 회귀') else '제한적', '소요(초)': round(time.time() - t0, 1)})
MCOMP = pd.DataFrame(rows); MCOMP.round(3)
""")
md("### 11-3. 성별 분리 단계 — 여성 고립을 놓치지 않도록")
code(r"""
U['성별 내 단계'] = pd.concat([levels(g) for _, g in U.groupby(['지역', '성별'])])
hi = ['심각', '경계']
comp = pd.DataFrame({'지역 내 단계': U[U['단계'].isin(hi)].groupby('지역')['성별'].apply(lambda s: (s == '여성').mean() * 100),
                     '성별 내 단계': U[U['성별 내 단계'].isin(hi)].groupby('지역')['성별'].apply(lambda s: (s == '여성').mean() * 100)})
print("'심각·경계' 집단 중 여성 집단 비율(%)"); display(comp.round(1))
for r in MAIN:
    print(f'[{r}] 여성 우선 지원 대상 상위 5 (성별 내 단계)')
    display(U[(U['지역'] == r) & (U['성별'] == '여성')].sort_values('위험점수', ascending=False)[['읍면동명', '연령5', '주민', '위험점수', '단계', '성별 내 단계', '주요 요인']].head(5).round(2))
SAVE2 = SAVE + ['성별 내 단계']
U[SAVE2].sort_values('위험점수', ascending=False).to_csv(OUTD / '위험도_강남_춘천_동x성x연령.csv', index=False, encoding='utf-8-sig')
print('저장 갱신: outputs/위험도_강남_춘천_동x성x연령.csv (성별 내 단계 추가)')
""")

md("## 12. 요약 · 다음 단계")
SUM = Path(__file__).with_name('s4.md')
md(SUM.read_text(encoding='utf-8') if SUM.exists() else '_(실행 결과를 바탕으로 작성)_')

if hasattr(flow, 'FLOW4'):
    flow.annotate(cells, flow.FLOW4, flow.TITLE4)
nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}})
nbf.write(nb, OUT); print('written', OUT, len(cells))
