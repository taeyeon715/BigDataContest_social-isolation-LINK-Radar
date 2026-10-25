import sys
from pathlib import Path
import nbformat as nbf

import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent)); import flow

OUT = Path(__file__).resolve().parent.parent / '01_데이터준비_품질점검.ipynb'
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md(r"""
# 01 노트북 데이터 개요·품질 점검: 2026 빅콘테스트 (사회적 고립 예방 AI 에이전트)

| 데이터 | 내용 | 단위 / 범위 |
|---|---|---|
| **SK텔레콤 유동인구** | 성·연령 / 시간대 / 요일별 일평균 유동인구 | 50m 격자(셀), 월별, 2025.07~12 |
| **신한카드 결제** | 가맹점 업종별 결제금액·건수 (오프라인) | 일별, 서울 강남구 · 강원 춘천시, 2025.07~12 |

- 입력: `convert_to_csv.py`로 변환한 `csv/` 폴더 (헤더 = 테이블 정의서의 한글 컬럼명)
- 목차: **1. 개요·품질 점검 → 2. SK 유동인구 → 3. 신한카드 → 4. 두 데이터 결합 미리보기 → 5. 요약·전처리 권고**
""")

code(r"""
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.colors import LinearSegmentedColormap, LogNorm

plt.rcParams.update({
    'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'figure.dpi': 110,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'axes.axisbelow': True,
    'grid.color': '#e6e5e1', 'grid.linewidth': 0.6, 'axes.edgecolor': '#b5b4ae',
    'axes.labelcolor': '#52514e', 'xtick.color': '#52514e', 'ytick.color': '#52514e',
    'axes.titlesize': 12, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left',
    'legend.frameon': False, 'lines.linewidth': 2,
})
pd.set_option('display.max_columns', 50); pd.set_option('display.max_rows', 100); pd.set_option('display.width', 200)
pd.set_option('display.float_format', '{:,.2f}'.format)

DATA = Path('csv')
MAIN = ['서울 강남구', '강원 춘천시']                     # 분석 대상 두 지역
C = {'서울 강남구': '#2a78d6', '강원 춘천시': '#eb6834'}   # 지역 색 (전 차트 공통)
GRAY, INK2 = '#b5b4ae', '#52514e'
SEQ = LinearSegmentedColormap.from_list('seq', ['#cde2fb', '#86b6ef', '#3987e5', '#1c5cab', '#0d366b'])
""")

md(r"""
## 1. 데이터 개요 & 품질 점검
""")

code(r"""
%%time
sk_dtype = {'기준년월': str, '소지역코드': str}
age = pd.read_csv(DATA / 'SK_유동인구_성연령.csv', dtype=sk_dtype)
tm  = pd.read_csv(DATA / 'SK_유동인구_시간대.csv', dtype=sk_dtype)
wk  = pd.read_csv(DATA / 'SK_유동인구_요일.csv', dtype=sk_dtype)
s1  = pd.read_csv(DATA / '신한카드_데이터1_시간대별.csv', dtype={'기준일자': str})
s2  = pd.read_csv(DATA / '신한카드_데이터2_고객거주지별.csv', dtype={'기준일자': str})
coldef = pd.read_csv(DATA / '컬럼정의.csv')
""")

md("### 1-1. 컬럼 정의 (테이블 정의서 기준)")
code("coldef")

md("### 1-2. 파일별 규모 · 결측 · 중복")
code(r"""
def profile(name, d, key):
    return {'데이터': name, '행 수': len(d), '열 수': d.shape[1],
            '결측 셀': int(d.isna().sum().sum()), '완전중복 행': int(d.duplicated().sum()),
            'PK 중복': int(d.duplicated(key).sum()), '메모리(MB)': d.memory_usage(deep=True).sum() / 1e6}

SK_KEY = ['기준년월', '소지역코드', 'X좌표', 'Y좌표']
S_KEY = ['기준일자', '가맹점 위치정보(시·군·구)', '가맹점 업종', '이용고객 성별', '이용고객 연령대']
pd.DataFrame([
    profile('SK 성연령', age, SK_KEY), profile('SK 시간대', tm, SK_KEY), profile('SK 요일', wk, SK_KEY),
    profile('신한 데이터1(시간대별)', s1, S_KEY + ['시간대']),
    profile('신한 데이터2(거주지별)', s2, S_KEY + ['이용고객 위치정보(거주지, 시도)']),
]).set_index('데이터')
""")

md(r"""
> **원본 이슈 (변환 단계에서 처리됨)**
> - SK `flow_time_pop_202512.csv`(185,090행), `flow_wkdy_pop_202512.csv`(242,534행)는 **모든 행이 정확히 2번씩** 들어 있었음 → 완전중복 제거 후 92,545행 / 121,267행 (다른 달과 같은 규모).
> - 그 외 결측·PK 중복 없음. 신한카드 금액·건수는 정의서상 `VARCHAR`지만 전부 정수 → 숫자형으로 저장.
""")

md("### 1-3. SK 수치 컬럼 점검 (음수 · 0 값)")
code(r"""
AGE_M = [c for c in age.columns if c.startswith('남자')]
AGE_F = [c for c in age.columns if c.startswith('여자')]
TM = [f'유동인구수_{h:02d}시간대' for h in range(24)]
WD = [f'{d}요일 유동인구' for d in '월화수목금토일']
num = {'성연령': (age, AGE_M + AGE_F), '시간대': (tm, TM), '요일': (wk, WD)}
pd.DataFrame({k: {'음수 값 수': int((d[c] < 0).sum().sum()),
                  '0 값 비율(%)': (d[c] == 0).mean().mean() * 100,
                  '전부 0인 행 수': int((d[c].sum(axis=1) == 0).sum()),
                  '최댓값': d[c].max().max()} for k, (d, c) in num.items()}).T
""")

md(r"""
## 2. SK 유동인구

### 2-1. 지역 구성
`소지역코드`(20자리)의 앞 5자리는 통계청(SGIS) 시군구 코드, 앞 8자리는 읍면동 코드로 보입니다 (시군구명은 코드 체계로 **추정**한 값).
""")
code(r"""
SGG = {'11230': '서울 강남구', '32010': '강원 춘천시',
       '11030': '서울 용산구', '11040': '서울 성동구', '11220': '서울 서초구', '11240': '서울 송파구',
       '31370': '경기 가평군', '32310': '강원 홍천군', '32370': '강원 화천군', '32380': '강원 양구군'}
for d in (age, tm, wk):
    d['시군구'] = d['소지역코드'].str[:5].map(SGG)

(age[age['기준년월'] == '202507'].groupby('시군구')
   .agg(셀수=('X좌표', 'size'), 소지역수=('소지역코드', 'nunique'),
        읍면동수=('소지역코드', lambda s: s.str[:8].nunique()))
   .sort_values('셀수', ascending=False))
""")
md(r"""
- 강남구(약 1.5만 셀)·춘천시(약 8.3만 셀)가 핵심이며, 인접 시군구 코드는 경계에 걸친 **수십 개 셀** 수준 → 이하 분석은 두 지역만 사용합니다.
- 테이블마다 셀 수가 다릅니다 (아래). 요일 테이블에만 있는 셀은 대부분 유동인구 0.1명 미만의 희소 셀입니다.
""")
code(r"""
cnt = pd.DataFrame({'성연령': age.groupby('기준년월').size(), '시간대': tm.groupby('기준년월').size(),
                    '요일': wk.groupby('기준년월').size()})
k = ['기준년월', 'X좌표', 'Y좌표']
ka, kt, kw = (set(map(tuple, d[k].values)) for d in (age, tm, wk))
print(f'셀 교집합(성연령∩시간대∩요일): {len(ka & kt & kw):,} / 요일에만: {len(kw - ka - kt):,} / 성연령에만: {len(ka - kt - kw):,}')
cnt
""")

md(r"""
### 2-2. 셀 단위 분포: 극단적으로 치우친 분포
`총유동인구` = 성·연령 12개 컬럼 합 (셀의 일평균 유동인구). 셀 간 합계는 중복 방문이 포함된 **활동량 지표**로 보는 것이 안전합니다.
""")
code(r"""
age['총유동인구'] = age[AGE_M + AGE_F].sum(axis=1)
tm['시간대합'] = tm[TM].sum(axis=1)
A = age[age['시군구'].isin(MAIN)]

def top_share(x, q):
    x = np.sort(x)[::-1]; return x[:max(1, int(len(x) * q))].sum() / x.sum() * 100

stats = A.groupby('시군구')['총유동인구'].agg(['count', 'mean', 'median', lambda s: s.quantile(.99), 'max'])
stats.columns = ['셀-월 수', '평균', '중앙값', 'p99', '최댓값']
stats['상위 1% 셀 비중(%)'] = A.groupby('시군구')['총유동인구'].apply(top_share, q=.01)
stats['상위 10% 셀 비중(%)'] = A.groupby('시군구')['총유동인구'].apply(top_share, q=.10)
display(stats.loc[MAIN])

fig, ax = plt.subplots(figsize=(9, 3.6))
bins = np.linspace(-2, 4.5, 66)
for r in MAIN:
    x = A.loc[(A['시군구'] == r) & (A['총유동인구'] > 0), '총유동인구']
    ax.hist(np.log10(x), bins=bins, histtype='step', lw=2, color=C[r], label=r, density=True)
ax.set_xticks(range(-2, 5)); ax.set_xticklabels(['0.01', '0.1', '1', '10', '100', '1,000', '10,000'])
ax.set(xlabel='셀 일평균 총유동인구 (로그 축)', ylabel='밀도', title='셀별 총유동인구 분포')
ax.legend(); plt.show()
""")
md(r"""
- 춘천은 셀 대부분이 하천·산지·도로변의 희소 셀이라 중앙값이 1.2명, 상위 10% 셀이 전체의 87%를 차지합니다 (강남 중앙값 118명).
- 저값 구간의 뾰족한 막대는 값이 **약 0.025 단위로 양자화**(0.03, 0.05, 0.08, 0.10 …)되어 있기 때문입니다: 원본 값 자체가 확대(가중) 추정치로 보입니다.
""")

md("### 2-3. 월별 추이 (2025-07 = 100 지수)")
code(r"""
mon = A.groupby(['기준년월', '시군구'])['총유동인구'].sum().unstack()[MAIN]
display((mon / 1e3).round(1).rename(columns=lambda c: c + ' (천명)'))
idx = mon / mon.iloc[0] * 100
fig, ax = plt.subplots(figsize=(8, 3.4))
for r in MAIN:
    ax.plot(idx.index, idx[r], marker='o', ms=6, color=C[r], label=r)
    ax.annotate(f'{idx[r].iloc[-1]:.1f}', (idx.index[-1], idx[r].iloc[-1]), xytext=(6, 0),
                textcoords='offset points', va='center', color=INK2)
ax.axhline(100, color=GRAY, lw=0.8)
ax.set(ylabel='지수 (7월=100)', title='월별 총유동인구 지수'); ax.legend(); plt.show()
""")

md("### 2-4. 성·연령 구조")
code(r"""
labels = ['10대', '20대', '30대', '40대', '50대', '60대+']
fig, axes = plt.subplots(1, 2, figsize=(11, 3.8), sharey=True)
pyr = {}
for ax, r in zip(axes, MAIN):
    d = A[A['시군구'] == r]; tot = d[AGE_M + AGE_F].sum().sum()
    m, f = d[AGE_M].sum().values / tot * 100, d[AGE_F].sum().values / tot * 100
    pyr[r] = pd.DataFrame({'남성(%)': m, '여성(%)': f}, index=labels)
    y = np.arange(len(labels))
    ax.barh(y, -m, color=C[r], height=.72); ax.barh(y, f, color=C[r], height=.72, alpha=.5)
    lim = max(m.max(), f.max()) * 1.15; ax.set_xlim(-lim, lim)
    ax.xaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f'{abs(v):.0f}%'))
    ax.set_yticks(y, labels); ax.axvline(0, color=INK2, lw=.8)
    ax.set_title(r); ax.text(-lim * .95, 5.5, '◀ 남성', color=INK2); ax.text(lim * .95, 5.5, '여성 ▶', color=INK2, ha='right')
plt.tight_layout(); plt.show()
pd.concat(pyr, axis=1).round(1)
""")

md("### 2-5. 시간대 패턴 (하루 중 비중)")
code(r"""
T = tm[tm['시군구'].isin(MAIN)].groupby('시군구')[TM].sum().loc[MAIN]
tshare = T.div(T.sum(axis=1), axis=0) * 100
fig, ax = plt.subplots(figsize=(9, 3.6))
peaks = []
for r in MAIN:
    ax.plot(range(24), tshare.loc[r].values, color=C[r], label=r, marker='o', ms=4)
    h = int(tshare.loc[r].values.argmax()); peaks.append(f'{r[3:]} {h}시 {tshare.loc[r].iloc[h]:.1f}%')
ax.text(.01, .93, '피크: ' + ' / '.join(peaks), transform=ax.transAxes, color=INK2, fontsize=9)
ax.set_xticks(range(0, 24, 2)); ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
ax.set(xlabel='시간대', ylabel='하루 유동인구 중 비중', title='시간대별 유동인구 비중'); ax.legend(); plt.show()
""")

md("### 2-6. 요일 패턴 (월~금 평균 = 100)")
code(r"""
W = wk[wk['시군구'].isin(MAIN)].groupby('시군구')[WD].sum().loc[MAIN]
widx = W.div(W[WD[:5]].mean(axis=1), axis=0) * 100
fig, ax = plt.subplots(figsize=(8, 3.4))
x = np.arange(7); w = .38
for i, r in enumerate(MAIN):
    ax.bar(x + (i - .5) * w, widx.loc[r], width=w - .02, color=C[r], label=r)
ax.set_xticks(x, list('월화수목금토일')); ax.axhline(100, color=INK2, lw=.8)
ax.set(ylabel='지수', title='요일별 유동인구 지수 (평일 평균=100)', ylim=(0, 125)); ax.legend(ncol=2); plt.show()
widx.round(1)
""")

md(r"""
### 2-7. 공간 분포 (6개월 평균, 50m 격자)
좌표는 UTM-K(EPSG:5179) 미터 단위이고 X·Y가 50m 간격이라 격자 이미지로 바로 그릴 수 있습니다.
""")
code(r"""
def grid_image(d, val):
    x0, y0 = d['X좌표'].min(), d['Y좌표'].min()
    gx = ((d['X좌표'] - x0) / 50).round().astype(int); gy = ((d['Y좌표'] - y0) / 50).round().astype(int)
    img = np.full((gy.max() + 1, gx.max() + 1), np.nan); img[gy, gx] = d[val].values
    return img, (x0 - 25, d['X좌표'].max() + 25, y0 - 25, d['Y좌표'].max() + 25)

cell = A.groupby(['시군구', 'X좌표', 'Y좌표'], as_index=False)['총유동인구'].mean()
fig, axes = plt.subplots(1, 2, figsize=(12, 5.6), gridspec_kw={'width_ratios': [1, 1]})
for ax, r in zip(axes, MAIN):
    img, ext = grid_image(cell[cell['시군구'] == r], '총유동인구')
    im = ax.imshow(np.clip(img, .1, None), origin='lower', extent=ext, cmap=SEQ, norm=LogNorm(.1, np.nanmax(img)))
    ax.set_title(r); ax.set_aspect('equal'); ax.grid(False); ax.set_xticks([]); ax.set_yticks([])
    for s in ax.spines.values(): s.set_visible(False)
    km = 1000 if r == '서울 강남구' else 5000
    ax.plot([ext[0] + 300, ext[0] + 300 + km], [ext[2] + 300] * 2, color=INK2, lw=2)
    ax.text(ext[0] + 300 + km / 2, ext[2] + 500, f'{km // 1000}km', ha='center', color=INK2, fontsize=9)
    cb = fig.colorbar(im, ax=ax, shrink=.7, label='셀 일평균 총유동인구 (로그)')
    cb.ax.yaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f'{v:,g}'))
plt.tight_layout(); plt.show()
""")

md("### 2-8. 유동인구 상위 소지역 (월평균, 소지역 내 셀 합)")
code(r"""
blk = (A.groupby(['시군구', '소지역코드', '기준년월'])['총유동인구'].sum()
         .groupby(['시군구', '소지역코드']).mean().rename('월평균 총유동인구').reset_index())
blk['읍면동코드(추정)'] = blk['소지역코드'].str[:8]
blk['지역 내 비중(%)'] = blk['월평균 총유동인구'] / blk.groupby('시군구')['월평균 총유동인구'].transform('sum') * 100
pd.concat({r: blk[blk['시군구'] == r].nlargest(10, '월평균 총유동인구').drop(columns='시군구').reset_index(drop=True)
           for r in MAIN})
""")

md(r"""
### 2-9. 세 테이블 간 정합성
같은 (월, 셀)에서 성연령 합계 · 시간대 합 · 요일 평균을 비교합니다.
""")
code(r"""
wk['요일평균'] = wk[WD].mean(axis=1)
k = ['기준년월', 'X좌표', 'Y좌표']
mm = age[k + ['총유동인구']].merge(tm[k + ['시간대합']], on=k).merge(wk[k + ['요일평균']], on=k)
print('결합 셀 수:', f'{len(mm):,}')
display(mm[['총유동인구', '시간대합', '요일평균']].corr(method='spearman').round(3))
pos = mm[mm['총유동인구'] > 1]
for c in ['시간대합', '요일평균']:
    q = (pos[c] / pos['총유동인구']).quantile([.05, .5, .95]).round(3).tolist()
    print(f'{c} / 총유동인구 비율  p5·중앙값·p95 = {q}')
""")
md(r"""
→ 세 테이블은 **같은 셀·월의 일평균 유동인구를 성연령 / 시간대 / 요일 축으로 나눈 것**입니다 (합계가 사실상 동일). 셀 단위로 서로 결합해 써도 됩니다.
""")

md(r"""
## 3. 신한카드 결제
분석 편의상 컬럼명을 짧게 바꿉니다 (CSV 원본 헤더는 그대로).
""")
code(r"""
REN = {'기준일자': '일자', '가맹점 위치정보(시·군·구)': '지역', '가맹점 업종': '업종',
       '이용고객 위치정보(거주지, 시도)': '거주지', '이용고객 성별': '성별', '이용고객 연령대': '연령대',
       '결제금액 합계': '금액', '결제건수 합계': '건수'}
s1, s2 = s1.rename(columns=REN), s2.rename(columns=REN)
for d in (s1, s2):
    d['일자'] = pd.to_datetime(d['일자'], format='%Y%m%d')
    d['월'] = d['일자'].dt.strftime('%Y-%m')
    d['객단가'] = d['금액'] / d['건수']
EOK = 1e8  # 억원
for col in ['시간대', '지역', '성별', '연령대']:
    print(f'[데이터1] {col}:', s1[col].value_counts().to_dict())
print('[데이터2] 거주지:', s2['거주지'].value_counts().to_dict())
print(f"업종 수: 데이터1 {s1['업종'].nunique()} / 데이터2 {s2['업종'].nunique()} / 데이터1에만:", set(s1['업종']) - set(s2['업종']))
print('기간:', s1['일자'].min().date(), '~', s1['일자'].max().date(), f"({s1['일자'].nunique()}일)")
""")

md(r"""
### 3-1. 결제건수는 '확대 추정값'으로 보임
`건수` 최솟값은 5이고, 작은 값 구간에서 **5·6 / 11~13 / 16~19 / 22~26 …처럼 띄엄띄엄** 나타납니다 (7~10, 14~15, 20~21은 개인 결제에서 0회).
실제 건수 1, 2, 3, 4…에 약 5~6배 계수를 곱해 반올림한 패턴과 일치 → **실제 건수가 아니라 확대(시장 보정 등) 추정치로 추정**되며, 최솟값 5는 '실제 1건' 수준으로 보입니다.
건수·금액은 절대값보다 **비중·지수·비율**로 해석하는 것이 안전합니다.
""")
code(r"""
v = s2['건수'].value_counts().reindex(range(5, 41), fill_value=0)
fig, ax = plt.subplots(figsize=(10, 3))
ax.bar(v.index, v.values / 1e3, color=C['서울 강남구'], width=.8)
for x in v.index[v.values == 0]: ax.text(x, 2, '0', ha='center', color=INK2, fontsize=8)
ax.set(xlabel='결제건수 값', ylabel='행 수 (천)', title='데이터2 결제건수 값의 빈도 (5~40)'); ax.set_xticks(range(5, 41, 1))
ax.tick_params(axis='x', labelsize=7); plt.show()
cov = s1.groupby(['지역', '업종'])['일자'].nunique()
print('업종별 관측 일수(184일 중) 분포')
pd.cut(cov, [0, 30, 90, 150, 183, 184], labels=['~30일', '31~90일', '91~150일', '151~183일', '184일(매일)']).groupby('지역').value_counts().unstack()
""")

md(r"""
### 3-2. 데이터1 ↔ 데이터2 관계
데이터1(법인 포함, 시간대별)에서 **법인을 빼면 데이터2(법인 제외, 거주지별)와 합계가 거의 일치**합니다. 금액은 원 단위까지 거의 같고(차이 수천 원), 건수만 0.1% 미만 차이 → 같은 결제를 다른 축(시간대 vs 고객 거주지)으로 나눈 것이며, 건수 차이는 행별 반올림 때문으로 보입니다.
""")
code(r"""
chk = pd.concat({'데이터1 전체': s1.groupby('지역')[['금액', '건수']].sum(),
                 '데이터1 법인제외': s1[s1['성별'] != '법인'].groupby('지역')[['금액', '건수']].sum(),
                 '데이터2': s2.groupby('지역')[['금액', '건수']].sum()}, axis=1)
chk.loc[:, (slice(None), '금액')] = chk.loc[:, (slice(None), '금액')] / EOK
display(chk.rename(columns={'금액': '금액(억원)'}))
corp = s1.assign(법인=s1['성별'] == '법인').groupby(['지역', '법인'])[['금액', '건수']].sum()
print('법인 결제 비중(%)')
(corp.xs(True, level='법인') / corp.groupby('지역').sum() * 100).round(1)
""")

md(r"""
### 3-3. 이상치: 금액 상위 행
`ZZ_나머지`(법인)·`세금공과금` 업종에 한 행 수천억 원 규모의 결제가 있습니다 (예: 2025-12-29 강남 세금공과금 60대 남성 2,896억 원). 개인의 일상 소비 패턴 분석에는 왜곡 요인입니다.
""")
code(r"""
display(s1.nlargest(10, '금액')[['일자', '시간대', '지역', '업종', '성별', '연령대', '금액', '건수', '객단가']]
        .style.format({'금액': '{:,.0f}', '객단가': '{:,.0f}', '건수': '{:,}'}))
""")
code(r"""
ind = s1.groupby(['지역', '업종'])['금액'].sum()
ind_share = (ind / ind.groupby('지역').transform('sum') * 100).rename('금액 비중(%)')
EXCL = ['ZZ_나머지', '세금공과금', '보험', '상품권/복권', '통신요금(이동,시내전화)', '통신요금(PC통신,무선호출)',
        '학교등록금', '결제대행(PG)']   # 개인의 일상 소비로 보기 어려운 업종 (분석 목적에 맞게 조정)

fig, axes = plt.subplots(1, 2, figsize=(12, 5.2))
for ax, r in zip(axes, MAIN):
    t = ind_share.loc[r].nlargest(15)[::-1]
    ax.barh(t.index, t.values, color=[GRAY if i in EXCL else C[r] for i in t.index], height=.72)
    for y, v in enumerate(t.values): ax.text(v, y, f' {v:.1f}%', va='center', fontsize=8, color=INK2)
    ax.set_title(f'{r} · 전체 업종 Top15 (회색=제외 후보)'); ax.xaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
    ax.set_xlim(0, t.max() * 1.18)
plt.tight_layout(); plt.show()
""")

md(r"""
이후 분석은 **법인 제외 + 위 `EXCL` 업종 제외**(= 개인 고객의 일반 업종 소비) 기준으로 진행합니다.
""")
code(r"""
b1 = s1[(s1['성별'] != '법인') & ~s1['업종'].isin(EXCL)].copy()
b2 = s2[~s2['업종'].isin(EXCL)].copy()
kept = (b1.groupby('지역')['금액'].sum() / s1.groupby('지역')['금액'].sum() * 100).round(1)
print('분석 대상 금액 비중(%):', kept.to_dict())
print('분석 대상 총액(억원):', (b1.groupby('지역')['금액'].sum() / EOK).round(0).to_dict())
""")

md("### 3-4. 일별 매출 추이")
code(r"""
daily = b1.groupby(['일자', '지역'])['금액'].sum().unstack()[MAIN] / EOK
fig, axes = plt.subplots(2, 1, figsize=(11, 5.6), sharex=True)
for ax, r in zip(axes, MAIN):
    ax.plot(daily.index, daily[r], color=C[r], lw=1, alpha=.4, label='일별')
    ax.plot(daily.index, daily[r].rolling(7, center=True).mean(), color=C[r], lw=2, label='7일 이동평균')
    ax.axvspan(pd.Timestamp('2025-10-03'), pd.Timestamp('2025-10-09'), color='#f0efec', zorder=0)
    ax.set_title(f'{r} 일별 결제금액 (억원)'); ax.set_ylim(0, None)
axes[0].text(pd.Timestamp('2025-10-06'), axes[0].get_ylim()[1] * .92, '추석 연휴', ha='center', color=INK2, fontsize=9)
axes[0].legend(loc='lower left', ncol=2); plt.tight_layout(); plt.show()

mon = b1.groupby(['월', '지역'])['금액'].sum().unstack()[MAIN] / EOK
pd.concat({'월 금액(억원)': mon, '전월비(%)': mon.pct_change() * 100}, axis=1).round(1)
""")

md("### 3-5. 요일 패턴 (평일 평균 = 100)")
code(r"""
dd = b1.groupby(['일자', '지역'])['금액'].sum().unstack()[MAIN]
dow = dd.groupby(dd.index.dayofweek).mean()
didx = dow / dow.iloc[:5].mean() * 100
fig, ax = plt.subplots(figsize=(8, 3.4)); x = np.arange(7); w = .38
for i, r in enumerate(MAIN):
    ax.bar(x + (i - .5) * w, didx[r], width=w - .02, color=C[r], label=r)
ax.set_xticks(x, list('월화수목금토일')); ax.axhline(100, color=INK2, lw=.8)
ax.set(ylabel='지수', title='요일별 결제금액 지수 (평일 평균=100)', ylim=(0, 125)); ax.legend(ncol=2); plt.show()
didx.T.round(1).set_axis(list('월화수목금토일'), axis=1)
""")

md("### 3-6. 시간대 패턴 (6시간 단위)")
code(r"""
TG = ['00_05', '06_11', '12_17', '18_23']
tg = b1.groupby(['지역', '시간대'])[['금액', '건수']].sum()
tg_share = (tg / tg.groupby('지역').transform('sum') * 100)
fig, axes = plt.subplots(1, 2, figsize=(11, 3.4), sharey=True); x = np.arange(4); w = .38
for ax, m in zip(axes, ['금액', '건수']):
    for i, r in enumerate(MAIN):
        ax.bar(x + (i - .5) * w, tg_share.loc[r, m].reindex(TG), width=w - .02, color=C[r], label=r)
    ax.set_xticks(x, ['00~05시', '06~11시', '12~17시', '18~23시']); ax.set_title(f'시간대별 {m} 비중')
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
axes[0].legend(); plt.tight_layout(); plt.show()
tg_share.unstack(0).round(1)
""")

md("### 3-7. 업종 구성 · 객단가")
code(r"""
bi = b1.groupby(['지역', '업종'])[['금액', '건수']].sum()
bi['금액 비중(%)'] = bi['금액'] / bi.groupby('지역')['금액'].transform('sum') * 100
bi['객단가(원)'] = bi['금액'] / bi['건수']
fig, axes = plt.subplots(1, 2, figsize=(12, 5.2))
for ax, r in zip(axes, MAIN):
    t = bi.loc[r, '금액 비중(%)'].nlargest(15)[::-1]
    ax.barh(t.index, t.values, color=C[r], height=.72)
    for y, v in enumerate(t.values): ax.text(v, y, f' {v:.1f}%', va='center', fontsize=8, color=INK2)
    ax.set_title(f'{r} · 분석 대상 업종 Top15'); ax.set_xlim(0, t.max() * 1.18)
    ax.xaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
plt.tight_layout(); plt.show()
pd.concat({r: bi.loc[r].nlargest(10, '금액 비중(%)')[['금액 비중(%)', '객단가(원)']].reset_index() for r in MAIN}, axis=1).round(1)
""")

md("### 3-8. 고객 성·연령 구조 (금액 비중)")
code(r"""
AG = ['10 대', '20 대', '30 대', '40 대', '50 대', '60 대', '70 대', '80 대', '90 대']
sa = b1.groupby(['지역', '성별', '연령대'])['금액'].sum()
sa = sa / sa.groupby('지역').transform('sum') * 100
fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), sharey=True)
for ax, r in zip(axes, MAIN):
    m = sa.loc[(r, '남성')].reindex(AG).values; f = sa.loc[(r, '여성')].reindex(AG).values
    y = np.arange(len(AG))
    ax.barh(y, -m, color=C[r], height=.72); ax.barh(y, f, color=C[r], height=.72, alpha=.5)
    lim = np.nanmax([m.max(), f.max()]) * 1.15; ax.set_xlim(-lim, lim)
    ax.xaxis.set_major_formatter(mtick.FuncFormatter(lambda v, _: f'{abs(v):.0f}%'))
    ax.set_yticks(y, [a.replace(' ', '') for a in AG]); ax.axvline(0, color=INK2, lw=.8); ax.set_title(r)
    ax.text(-lim * .95, 8.4, '◀ 남성', color=INK2); ax.text(lim * .95, 8.4, '여성 ▶', color=INK2, ha='right')
plt.tight_layout(); plt.show()
sa.unstack([0, 1]).reindex(AG).round(1)
""")

md(r"""
### 3-9. 고객 거주지 (데이터2): 외지인 소비
외지인 = 가맹점 소재 시도와 다른 시도 거주 고객 (강남: 서울 외 / 춘천: 강원 외, `정보없음` 제외).
""")
code(r"""
HOME = {'서울 강남구': '서울', '강원 춘천시': '강원'}
b2['외지인'] = b2['거주지'] != b2['지역'].map(HOME)
known = b2[b2['거주지'] != '정보없음']
res = known.groupby(['지역', '거주지'])['금액'].sum()
res = (res / res.groupby('지역').transform('sum') * 100)
fig, axes = plt.subplots(1, 2, figsize=(12, 4.4))
for ax, r in zip(axes, MAIN):
    t = res.loc[r].nlargest(10)[::-1]
    ax.barh(t.index, t.values, color=[GRAY if i == HOME[r] else C[r] for i in t.index], height=.72)
    for y, v in enumerate(t.values): ax.text(v, y, f' {v:.1f}%', va='center', fontsize=8, color=INK2)
    ax.set_title(f'{r} · 고객 거주지별 금액 비중 (회색=관내)'); ax.set_xlim(0, t.max() * 1.18)
    ax.xaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
plt.tight_layout(); plt.show()

out_m = known.groupby(['월', '지역']).apply(lambda d: d.loc[d['외지인'], '금액'].sum() / d['금액'].sum() * 100).unstack()[MAIN]
print('월별 외지인 금액 비중(%)'); display(out_m.round(1))
""")
code(r"""
# 업종별 외지인 비중 (지역 금액의 0.5% 이상 업종만)
g = known.groupby(['지역', '업종']).apply(lambda d: pd.Series({
        '금액(억원)': d['금액'].sum() / EOK,
        '외지인 비중(%)': d.loc[d['외지인'], '금액'].sum() / d['금액'].sum() * 100}))
g['지역 내 비중(%)'] = g['금액(억원)'] / g.groupby('지역')['금액(억원)'].transform('sum') * 100
g = g[g['지역 내 비중(%)'] >= .5]
pd.concat({r: g.loc[r].nlargest(8, '외지인 비중(%)').reset_index() for r in MAIN}, axis=1).round(1)
""")

md(r"""
## 4. 유동인구 × 카드결제 결합 미리보기
SK 시간대(1시간)를 카드 시간대(6시간)로 묶어 강남구·춘천시 **유동인구 비중 vs 결제건수 비중**을 비교합니다.
""")
code(r"""
bucket = {'00_05': TM[0:6], '06_11': TM[6:12], '12_17': TM[12:18], '18_23': TM[18:24]}
pop6 = pd.DataFrame({b: T[cols].sum(axis=1) for b, cols in bucket.items()})
pop6 = pop6.div(pop6.sum(axis=1), axis=0) * 100
card6 = tg_share['건수'].unstack(1)[TG]
fig, axes = plt.subplots(1, 2, figsize=(11, 3.4), sharey=True); x = np.arange(4); w = .38
for ax, r in zip(axes, MAIN):
    ax.bar(x - w / 2, pop6.loc[r], width=w - .02, color=GRAY, label='유동인구 비중')
    ax.bar(x + w / 2, card6.loc[r], width=w - .02, color=C[r], label='결제건수 비중')
    ax.set_xticks(x, ['00~05시', '06~11시', '12~17시', '18~23시']); ax.set_title(r)
    ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0)); ax.legend(fontsize=9)
plt.tight_layout(); plt.show()
pd.concat({'유동인구(%)': pop6, '결제건수(%)': card6}, axis=1).round(1)
""")
code(r"""
# 월별: 유동인구 지수 vs 결제금액 지수 (7월=100, 같은 축)
pop_m = A.groupby(['기준년월', '시군구'])['총유동인구'].sum().unstack()[MAIN]
card_m = b1.groupby(['월', '지역'])['금액'].sum().unstack()[MAIN]
pop_m.index = card_m.index
fig, axes = plt.subplots(1, 2, figsize=(11, 3.4), sharey=True)
for ax, r in zip(axes, MAIN):
    ax.plot(pop_m.index, pop_m[r] / pop_m[r].iloc[0] * 100, color=GRAY, marker='o', ms=6, label='유동인구')
    ax.plot(card_m.index, card_m[r] / card_m[r].iloc[0] * 100, color=C[r], marker='o', ms=6, label='결제금액')
    ax.axhline(100, color=INK2, lw=.6); ax.set_title(f'{r} (7월=100)'); ax.legend(fontsize=9)
    ax.tick_params(axis='x', labelsize=8)
plt.tight_layout(); plt.show()
""")

md(r"""
## 5. 요약 및 전처리 권고

### 전처리 규칙과 이유 (02·03 노트북은 이 규칙을 그대로 따릅니다)

| 규칙 | 이유 | 근거 섹션 |
|---|---|---|
| ① 법인 결제 제외 | 주제는 '사람'의 고립인데 법인카드는 회사 업무 비용이라 개인 생활 패턴이 아닙니다. 성별·연령이 '법인'으로만 표시돼 성·연령 분석에 쓸 수 없고, 데이터2는 처음부터 법인이 빠져 있어 두 데이터의 기준을 맞추려면 빼야 합니다. 법인이 금액의 13~16%를 차지해 비중도 왜곡합니다. | 3-2 |
| ② 특수 업종 8개 제외 (ZZ_나머지·세금공과금·보험·상품권/복권·통신요금 2종·학교등록금·결제대행) | 의무적이거나 정기·자동 결제라 외출·사교 같은 생활 활동의 변화를 반영하지 않습니다. 강남 금액의 52.6%가 세금공과금+ZZ_나머지(한 행 최대 5,205억 원)라 그대로 두면 모든 비중이 두 업종에 끌려갑니다. ZZ_나머지는 업종 미분류, 결제대행은 온라인 결제입니다. | 3-3 |
| ③ 결제건수는 비율·지수로만 해석 | 값이 5·6 / 11~13 / 16~19처럼 띄엄띄엄 나와 실제 건수의 약 5~6배 확대 추정치로 보입니다. 절대값에는 의미가 없지만, 같은 방식으로 확대됐다면 집단 간 비중·지수 비교는 유지됩니다. 신한카드 점유율이 연령·지역마다 다르고 현금·타사 카드가 빠진 편향, 그리고 강남·춘천의 약 10배 규모 차이도 비율로 봐야 줄어듭니다. | 3-1 |
| ④ 금액보다 건수 위주 (02부터 적용) | 고립은 '얼마나 자주 활동하는가'의 문제입니다. 금액은 고가 업종(기타의료 객단가 약 265만 원 등)에 좌우돼 활동 빈도를 왜곡합니다. | 3-7 |
| ⑤ 강남구·춘천시만 분석 | 카드 데이터가 이 두 시군구뿐이라 결합할 수 있는 곳이 여기뿐입니다. SK에 섞인 인접 시군구 격자는 60개뿐이고 경계 일부라 그 지역을 대표하지 못합니다. | 2-1 |
| ⑥ SK 12월 시간대·요일 중복 제거 | 모든 행이 정확히 두 번씩 들어 있어 합치면 12월 유동인구가 2배가 됩니다. 중복을 지우면 행 수가 다른 달과 같은 규모가 되어 오류로 판단했습니다. | 1-2 |

### 데이터 구조
- **SK 유동인구 3개 테이블**은 같은 (기준년월, 50m 셀)의 일평균 유동인구를 성연령 / 시간대 / 요일로 나눈 것: 셀 단위 합계가 사실상 동일. 공통 키는 `(기준년월, X좌표, Y좌표)`, `소지역코드` 앞 5자리 = 시군구, 앞 8자리 = 읍면동(추정).
- **신한카드 데이터2 = 데이터1에서 법인을 뺀 뒤 고객 거주지 축으로 다시 집계한 것** (금액 합계가 원 단위까지 거의 일치).

### 품질 이슈
| 이슈 | 내용 | 조치 |
|---|---|---|
| SK 12월 중복 | 시간대·요일 202512 파일 전 행이 2번씩 | CSV 변환 시 제거 완료 |
| 인접 시군구 셀 | 용산·서초·가평·화천 등 경계 셀 60개(202507 기준) | 강남구(11230)·춘천시(32010)만 사용 |
| 테이블별 셀 커버리지 차이 | 요일 테이블에만 희소 셀 약 11.7만 개(셀-월) 추가 | 결합 시 inner join 또는 0 채움 기준 명시 |
| SK 값 양자화 | 약 0.025 단위, 0 값 비율 약 21% | 셀 단위보다 소지역·읍면동 집계 권장 |
| 카드 결제건수 확대값 | 5·6 / 11~13 / 16~19…처럼 띄엄띄엄 → 약 5~6배로 확대한 추정치로 보임 | 절대 건수 대신 비중·지수로 해석 |
| 금액 극단치 | 강남 금액의 52.6%가 `세금공과금`+`ZZ_나머지`, 한 행 최대 5,205억 원 | 법인·특수업종(`EXCL`) 분리 |
| 업종 관측일 부족 | 춘천 4개, 강남 2개 업종은 184일 중 30일 이하만 관측 | '없는 행 = 0' 처리 여부 결정 필요 |
| 본사 결제 의심 | 강남 `컴퓨터/소프트웨어`·`쇼핑몰` 외지인 비중 73~78% | 오프라인 상권 분석 시 별도 검토 |

### 주요 인사이트
- **유동인구 밀도**: 강남 셀 중앙값 118명 vs 춘천 1.2명. 춘천은 상위 10% 셀이 전체 유동인구의 87%를 차지해 시내 중심부에 강하게 집중됩니다.
- **연령 구조**: 강남은 20~40대, 특히 20대 여성(8.8%)이 두드러지고, 춘천은 60대 이상이 24.7%입니다.
- **요일**: 강남은 주말 유동인구가 크게 줄어듭니다(토 80, 일 66, 평일 평균=100). 춘천은 유동인구가 주말에도 평일 수준(토 99.5)인데 **결제금액은 토요일 121로 급증**해, 외부 방문형 주말 소비가 보입니다.
- **시간대**: 두 지역 모두 17시가 유동인구 피크입니다. 결제건수 비중은 유동인구보다 12~23시에 더 몰리고, 06~11시에는 낮습니다(유동 28% vs 결제 21%).
- **외지인 소비**: 강남 결제의 약 50%가 서울 밖 거주자(경기 24%)입니다. 춘천은 약 18%로, 10월에 20.7%로 가장 높고 12월에 14.2%로 가장 낮습니다. 춘천의 놀이동산·골프장은 외지인 비중이 80% 안팎입니다.
- **월별·이벤트**: 강남은 12월 결제가 전월 대비 +9.1%(연말)입니다. 춘천은 10월 +7.6%로, 추석 연휴 직전·초반에 급증합니다. 추석 연휴 중 강남 결제는 급감합니다.

### 다음 단계 제안
1. 카드 데이터는 **시군구 단위**만 있으므로 SK 셀을 시군구(또는 시간대 6구간)로 집계해 결합합니다. 셀 단위 상권 분석에는 업종·점포 위치 데이터를 추가로 확보해야 합니다.
2. 분석 기준(법인 제외, `EXCL` 업종 제외, 결측 조합 처리)을 확정한 뒤 **업종 × 요일 × 시간대 × 연령** 마트를 만듭니다.
3. 공휴일·이벤트 캘린더(추석, 크리스마스, 지역 축제)를 변수로 추가합니다.
""")

nb = nbf.v4.new_notebook(cells=cells, metadata={
    "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
    "language_info": {"name": "python"}})
flow.annotate(cells, flow.FLOW1, flow.TITLE1)
nb.cells = cells
nbf.write(nb, OUT)
print("written", OUT, len(cells), "cells")
