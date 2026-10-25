from pathlib import Path
import nbformat as nbf

import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent)); import flow

OUT = Path(__file__).resolve().parent.parent / '02_탐색분석_고립신호.ipynb'
SUMMARY = Path(__file__).with_name("s2.md")
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md(r"""
# 02 노트북 사회적 고립 위험 신호 탐색: 전 연령·성별·지역

**주제**: 사회적 고립 예방 및 대응을 위한 AI 에이전트 (2026 빅콘테스트 AI데이터 활용 분야)
**관점**: 고립은 노년층만의 문제가 아님: 청년(은둔·1인가구), 중년(50~60대 남성 고독사 고위험), 노년(독거) 모두를 같은 비중으로 탐색

| 데이터 | 공간 해상도 | 인구집단 | 시간 해상도 |
|---|---|---|---|
| SK 유동인구 (성연령 / 시간대 / 요일) | 50m 격자 → 읍면동(47개) | 성 × 연령 6구간 (10대~60대+) | 월 / 1시간 / 요일: **세 축은 서로 교차 불가** |
| 신한카드 데이터1 | 시군구 (강남구·춘천시) | 성 × 연령 9구간 (10대~90대) | 일 × 6시간 |
| 신한카드 데이터2 | 시군구 + 고객 거주 시도 | 성 × 연령 9구간 | 일 |

→ **지역 단위 신호는 SK(읍면동), 인구집단 단위 신호는 카드(성·연령)** 에서 주로 나옵니다.

**목차**
1. 결측 (명시적 · 암묵적)
2. 이상값
3. 연령별 분석
4. 성별 분석
5. 지역별 분석
6. 결합: 유동인구 대비 소비 참여도
7. 관찰 요약 · 신호 후보 · 한계
""")

# ------------------------------------------------------------------ setup
code(r"""
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

plt.rcParams.update({
    'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'figure.dpi': 110,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'axes.axisbelow': True,
    'grid.color': '#e6e5e1', 'grid.linewidth': 0.6, 'axes.edgecolor': '#b5b4ae',
    'axes.labelcolor': '#52514e', 'xtick.color': '#52514e', 'ytick.color': '#52514e',
    'axes.titlesize': 11.5, 'axes.titleweight': 'bold', 'axes.titlelocation': 'left',
    'legend.frameon': False, 'lines.linewidth': 2,
})
pd.set_option('display.max_columns', 60); pd.set_option('display.max_rows', 120); pd.set_option('display.width', 220)
pd.set_option('display.float_format', '{:,.2f}'.format)

DATA = Path('csv')
MAIN = ['서울 강남구', '강원 춘천시']
C = {'서울 강남구': '#2a78d6', '강원 춘천시': '#eb6834'}
GRAY, INK, INK2 = '#b5b4ae', '#0b0b0b', '#52514e'
SEQ = LinearSegmentedColormap.from_list('seq', ['#f4f8fd', '#9ec5f4', '#3987e5', '#1c5cab', '#0d366b'])
DIV = LinearSegmentedColormap.from_list('div', ['#1c5cab', '#86b6ef', '#f0efec', '#f2a9a8', '#c43a39'])
RAMP6 = ['#86b6ef', '#5598e7', '#2a78d6', '#1c5cab', '#104281', '#0d366b']     # 연령 6구간 (서열)
AGE6 = ['10대', '20대', '30대', '40대', '50대', '60대+']
AGE9 = ['10대', '20대', '30대', '40대', '50대', '60대', '70대', '80대', '90대']
LS = {'10대': '10대', '20대': '청년(20~30대)', '30대': '청년(20~30대)', '40대': '중년(40~50대)',
      '50대': '중년(40~50대)', '60대+': '노년(60대+)'}
STAGES = ['10대', '청년(20~30대)', '중년(40~50대)', '노년(60대+)']
STAGE_C = dict(zip(STAGES, ['#86b6ef', '#3987e5', '#1c5cab', '#0d366b']))
SEXES = [('남성', '-', 'o', 1.0), ('여성', '--', 's', .5)]   # 선 스타일 / 막대 투명도

def heat(ax, df, cmap=SEQ, fmt='{:.1f}', center=None, clip=None, vmin=None, vmax=None, fs=8, rot=0):
    v = df.values.astype(float)
    if center is not None:
        lim = clip if clip else np.nanmax(np.abs(v - center))
        norm = TwoSlopeNorm(vcenter=center, vmin=center - lim, vmax=center + lim)
    else:
        norm = plt.Normalize(np.nanmin(v) if vmin is None else vmin, np.nanmax(v) if vmax is None else vmax)
    im = ax.imshow(np.clip(v, norm.vmin, norm.vmax), cmap=cmap, norm=norm, aspect='auto')
    ax.set_xticks(range(df.shape[1]), [str(c) for c in df.columns], fontsize=fs, rotation=rot,
                  ha='right' if rot else 'center')
    ax.set_yticks(range(df.shape[0]), [str(i) for i in df.index], fontsize=fs)
    ax.grid(False); ax.tick_params(length=0)
    for s in ax.spines.values(): s.set_visible(False)
    for i in range(v.shape[0]):
        for j in range(v.shape[1]):
            if np.isnan(v[i, j]): continue
            t = norm(np.clip(v[i, j], norm.vmin, norm.vmax))
            dark = abs(t - .5) > .33 if center is not None else t > .55
            ax.text(j, i, fmt.format(v[i, j]), ha='center', va='center', fontsize=fs - 1.5,
                    color='white' if dark else INK)
    return im

def rs_lines(ax, df, x=None, labels=True):
    # df: index = x축, columns = (지역, 성별)
    x = list(df.index) if x is None else x
    for r in MAIN:
        for sx, ls, mk, _ in SEXES:
            if (r, sx) in df.columns:
                ax.plot(x, df[(r, sx)].values, color=C[r], ls=ls, marker=mk, ms=4.5, lw=1.8,
                        label=f'{r[3:]} {sx}' if labels else None)

def sex_bars(ax, df, r, x_labels):
    # df: index = 연령, columns = 성별 ; 남성 진하게 / 여성 연하게
    x = np.arange(len(df)); w = .4
    for i, (sx, _, _, a) in enumerate(SEXES):
        ax.bar(x + (i - .5) * w, df[sx].values, width=w - .03, color=C[r], alpha=a, label=sx)
    ax.set_xticks(x, x_labels)

def pct_axis(ax, axis='y', dec=0):
    (ax.yaxis if axis == 'y' else ax.xaxis).set_major_formatter(mtick.PercentFormatter(decimals=dec))
""")

md("### 데이터 로드 · 파생 변수")
code(r"""
%%time
# ---------------- SK 유동인구 (강남구 11230 · 춘천시 32010 만)
SGG = {'11230': '서울 강남구', '32010': '강원 춘천시'}
def load_sk(name):
    d = pd.read_csv(DATA / name, dtype={'기준년월': str, '소지역코드': str})
    d = d[d['소지역코드'].str[:5].isin(SGG)].copy()
    d['지역'] = d['소지역코드'].str[:5].map(SGG)
    d['읍면동'] = d['소지역코드'].str[:8]
    n = d['읍면동'].str[5:8].astype(int)
    d['유형'] = np.select([n < 300, n < 500], ['읍', '면'], '동')   # 코드 체계로 추정 (춘천 1읍 9면 15동, 강남 22동과 일치)
    return d
age, tm, wk = (load_sk(f) for f in ['SK_유동인구_성연령.csv', 'SK_유동인구_시간대.csv', 'SK_유동인구_요일.csv'])
MC = dict(zip(AGE6, [c for c in age.columns if c.startswith('남자')]))
FC = dict(zip(AGE6, [c for c in age.columns if c.startswith('여자')]))
ALL = list(MC.values()) + list(FC.values())
COLMAP = {**{c: ('남성', a) for a, c in MC.items()}, **{c: ('여성', a) for a, c in FC.items()}}
TM = [f'유동인구수_{h:02d}시간대' for h in range(24)]
WD = [f'{d}요일 유동인구' for d in '월화수목금토일']
age['총'] = age[ALL].sum(axis=1)

# 읍면동 × 월 × 성 × 연령 long 테이블
sk_long = (age.groupby(['지역', '유형', '읍면동', '기준년월'])[ALL].sum()
              .stack().rename('인구').reset_index().rename(columns={'level_4': '컬럼'}))
sk_long['성별'] = sk_long['컬럼'].map(lambda c: COLMAP[c][0])
sk_long['연령'] = sk_long['컬럼'].map(lambda c: COLMAP[c][1])
sk_long['생애단계'] = sk_long['연령'].map(LS)
sk_long = sk_long.drop(columns='컬럼')

# ---------------- 신한카드
REN = {'기준일자': '일자', '가맹점 위치정보(시·군·구)': '지역', '가맹점 업종': '업종',
       '이용고객 위치정보(거주지, 시도)': '거주지', '이용고객 성별': '성별', '이용고객 연령대': '연령대',
       '결제금액 합계': '금액', '결제건수 합계': '건수'}
GROUP = {}
for g, items in {
    '외식': ['한식', '양식', '일식', '중식', '기타요식', '패스트푸드', '제과점', '커피전문점'],
    '여가·문화': ['노래방', '영화/공연', '게임방/오락실', '종합레저타운/놀이동산', '서점', '문화용품', '악기/음반', '화원'],
    '스포츠·건강관리': ['스포츠시설', '헬스장', '실내/실외골프장', '스포츠/레저용품', '싸우나/목욕탕', '안마/마사지'],
    '음주·유흥': ['유흥업소', '주류판매'],
    '여행·교통': ['여행사/항공사', '호텔/콘도', '모텔,여관,기타숙박', '고속버스/철도/여객선', '택시', '면세점'],
    '미용·패션': ['미용실', '미용서비스', '화장품', '의복/의류', '패션잡화', '시계/귀금속'],
    '편의점': ['편의점'],
    '식료·생필품': ['할인점/슈퍼마켓/양판점', '기타식품', '농수산물', '정육점', '생활잡화/수입상품점', '세탁소'],
    '쇼핑·가정': ['백화점', '쇼핑몰', '가전', '가구', '컴퓨터/소프트웨어', '인테리어/건축자재/주방기구', '완구/아동용자전거',
               '전용매장', '중고품판매점', '수제용품점', '체인점', '기타유통', '사무기기/문구용품', '방문판매/다단계판매'],
    '의료': ['일반병원', '종합병원', '치과병원', '한의원', '기타의료', '약국', '보건소'],
    '반려동물': ['애완동물', '동물병원'],
    '교육·육아': ['학원/학습지', '독서실', '유치원', '학교등록금'],
    '차량·연료': ['주유소', 'LPG가스', '자동차서비스', '자동차용품', '주차장', '수입자동차', '중고차판매', '오토바이'],
    '공과금·금융·기타서비스': ['세금공과금', '보험', '통신요금(이동,시내전화)', '통신요금(PC통신,무선호출)', '상품권/복권',
                     '결제대행(PG)', '법률/사무서비스', '회계/변리서비스', '연구/번역서비스', '부동산중개',
                     '예식장/결혼서비스', '장례식장/묘지/장의사'],
    '기타(ZZ)': ['ZZ_나머지'],
}.items():
    GROUP.update({i: g for i in items})
GORDER = ['외식', '여가·문화', '스포츠·건강관리', '음주·유흥', '여행·교통', '미용·패션', '편의점', '식료·생필품',
          '쇼핑·가정', '의료', '반려동물', '교육·육아', '차량·연료', '공과금·금융·기타서비스']
OUTDOOR = ['외식', '여가·문화', '스포츠·건강관리', '음주·유흥', '여행·교통']        # 외부·사회활동형
EXCL = ['ZZ_나머지', '세금공과금', '보험', '상품권/복권', '통신요금(이동,시내전화)', '통신요금(PC통신,무선호출)',
        '학교등록금', '결제대행(PG)']                                             # 일상 소비로 보기 어려운 업종
HOL = pd.to_datetime(['2025-08-15', '2025-10-03', '2025-10-06', '2025-10-07', '2025-10-08', '2025-10-09', '2025-12-25'])
CHUSEOK = pd.date_range('2025-10-03', '2025-10-09')

def load_card(name):
    d = pd.read_csv(DATA / name, dtype={'기준일자': str}).rename(columns=REN)
    d['일자'] = pd.to_datetime(d['일자'], format='%Y%m%d')
    d['연령'] = d['연령대'].str.replace(' ', '')
    return d

def enrich(d):
    d = d[d['성별'] != '법인'].copy()
    d['연령6'] = np.where(d['연령'].isin(AGE6[:5]), d['연령'], '60대+')
    d['생애단계'] = d['연령6'].map(LS)
    d['기준년월'] = d['일자'].dt.strftime('%Y%m')
    dow = d['일자'].dt.dayofweek
    d['일유형'] = np.select([d['일자'].isin(HOL), dow >= 5], ['공휴일', '주말'], '평일')
    d['업종그룹'] = d['업종'].map(GROUP)
    d['일상소비'] = ~d['업종'].isin(EXCL)
    return d

s1 = load_card('신한카드_데이터1_시간대별.csv')
s2 = load_card('신한카드_데이터2_고객거주지별.csv')
assert set(s1['업종']) <= set(GROUP), set(s1['업종']) - set(GROUP)
p1, p2 = enrich(s1), enrich(s2)          # 개인(법인 제외)
q = p1[p1['일상소비']].copy()           # 일상 소비 (분석 기본 단위)
print(f'SK 성연령 {len(age):,}행 / sk_long {len(sk_long):,}행 / 카드 개인 {len(p1):,}행 (일상소비 {len(q):,}) / 데이터2 {len(p2):,}행')
""")

# ------------------------------------------------------------------ 1. 결측
md(r"""
## 1. 결측

### 1-1. 명시적 결측(NaN)과 '정보없음'
""")
code(r"""
display(pd.Series({'SK 성연령': age[ALL].isna().sum().sum(), 'SK 시간대': tm[TM].isna().sum().sum(),
                   'SK 요일': wk[WD].isna().sum().sum(), '카드 데이터1': s1.isna().sum().sum(),
                   '카드 데이터2': s2.isna().sum().sum()}, name='NaN 셀 수').to_frame())
g = p2.groupby(['연령', '지역', '성별', '거주지'])['건수'].sum()
na = (g.xs('정보없음', level='거주지').reindex(g.groupby(['연령', '지역', '성별']).sum().index).fillna(0)
      / g.groupby(['연령', '지역', '성별']).sum() * 100).unstack(['지역', '성별']).reindex(AGE9)
na.columns = [f'{r[3:]}·{s}' for r, s in na.columns]
fig, ax = plt.subplots(figsize=(6.5, 3.8))
fig.colorbar(heat(ax, na, fmt='{:.2f}'), ax=ax, shrink=.8, label='%')
ax.set_title("데이터2 고객 거주지 '정보없음' 비율(건수 %)"); plt.show()
""")
md("**명시적 결측(NaN)은 없습니다.** 데이터2의 거주지 '정보없음'도 최대 0.2% 수준이라 분석에 영향이 거의 없습니다. 실제 결측은 아래의 **암묵적 결측**(빠진 격자·빠진 조합)으로 나타납니다.")

md(r"""
### 1-2. SK: 격자 패널 결측 (6개월 중 관측된 달 수)
같은 50m 격자가 매달 관측되지는 않습니다. 관측된 달 수가 적은 격자는 유동인구 자체가 작습니다(기준 미만이라 빠진 것으로 추정).
""")
code(r"""
pres = age.groupby(['지역', 'X좌표', 'Y좌표']).agg(개월수=('기준년월', 'nunique'), 평균유동=('총', 'mean'))
t = pres.groupby(['지역', '개월수']).agg(셀수=('평균유동', 'size'), 평균유동인구=('평균유동', 'mean'), 중앙값=('평균유동', 'median'))
t['셀 비중(%)'] = t['셀수'] / t.groupby('지역')['셀수'].transform('sum') * 100
t
""")

md(r"""
### 1-3. SK: 성·연령 컬럼별 0값 비율
0은 '그 셀에서 해당 성·연령 유동인구가 측정 단위(약 0.025명) 미만'이라는 뜻으로, 사실상 **희소 결측**입니다.
""")
code(r"""
z = {}
for r in MAIN:
    d = age[age['지역'] == r]
    for sx, cmap_ in (('남성', MC), ('여성', FC)):
        z[f'{r[3:]}·{sx}'] = pd.Series({a: (d[c] == 0).mean() * 100 for a, c in cmap_.items()})
z = pd.DataFrame(z)
fig, ax = plt.subplots(figsize=(6.5, 3.2))
fig.colorbar(heat(ax, z), ax=ax, shrink=.8, label='%')
ax.set_title('SK 격자 0값 비율(%): 연령 × 성별'); plt.show()
""")

md(r"""
### 1-4. 카드: 조합 결측 (있어야 할 행 중 실제로 있는 비율)
기대 조합 = 184일 × 4시간대 × 업종 수(지역별) × (성·연령 그룹 1개). 행이 없다는 것은 **그 조합의 결제가 없었다(0건)** 는 뜻일 가능성이 큽니다 (건수 최솟값 5 = 실제 1건 추정, 01 노트북 참고).
""")
code(r"""
n_ind = p1.groupby('지역')['업종'].nunique()
obs = p1.groupby(['연령', '지역', '성별']).size()
cov = (obs / (184 * 4 * obs.index.get_level_values('지역').map(n_ind).values) * 100).unstack(['지역', '성별']).reindex(AGE9)
cov.columns = [r[3:] + chr(10) + s for r, s in cov.columns]
obs_t = p1.groupby(['연령', '지역', '시간대']).size()
cov_t = (obs_t / (184 * 2 * obs_t.index.get_level_values('지역').map(n_ind).values) * 100).unstack(['지역', '시간대']).reindex(AGE9)
cov_t.columns = [r[3:] + chr(10) + t for r, t in cov_t.columns]
fig, axes = plt.subplots(1, 2, figsize=(13, 4.2), gridspec_kw={'width_ratios': [4, 8]})
heat(axes[0], cov, vmin=0, vmax=100); axes[0].set_title('조합 관측률(%): 연령 × 성별')
im = heat(axes[1], cov_t, vmin=0, vmax=100); axes[1].set_title('조합 관측률(%): 연령 × 시간대')
fig.colorbar(im, ax=axes, shrink=.8, label='%'); plt.show()

obs_i = (p1.groupby(['지역', '업종']).size() / (184 * 4 * 18) * 100).rename('관측률(%)')
print('관측률이 낮은 업종 (하위 10)')
pd.concat({r: obs_i.loc[r].nsmallest(10).reset_index() for r in MAIN}, axis=1)
""")

# ------------------------------------------------------------------ 2. 이상값
md(r"""
## 2. 이상값

### 2-1. SK: 성·연령 컬럼별 분포와 극단값
로그 스케일 IQR 기준(Q3 + 3×IQR 초과)의 극단값 수입니다.
""")
code(r"""
rows = []
for r in MAIN:
    d = age[age['지역'] == r]
    for c in ALL:
        x = d[c]; lx = np.log10(x[x > 0]); q1_, q3_ = lx.quantile([.25, .75])
        rows.append({'지역': r[3:], '성별': COLMAP[c][0], '연령': COLMAP[c][1], '중앙값': x.median(),
                     'p99': x.quantile(.99), '최댓값': x.max(), '극단값 수': int((lx > q3_ + 3 * (q3_ - q1_)).sum())})
pd.DataFrame(rows).pivot_table(index=['성별', '연령'], columns='지역', values=['중앙값', 'p99', '최댓값', '극단값 수'])
""")

md(r"""
### 2-2. SK: 월별로 급변한 소지역
소지역별 월 합계의 최대/최소 비율입니다 (6개월 평균 100명 이상 소지역만).
""")
code(r"""
bm = age.groupby(['지역', '소지역코드', '기준년월'])['총'].sum().unstack()
bm = bm[bm.mean(axis=1) >= 100]
jump = bm.max(axis=1) / bm.min(axis=1)
print('최대/최소 비율 분포'); display(jump.groupby('지역').describe(percentiles=[.5, .9, .99]).round(2))
print('비율 2배 이상 소지역 수:', (jump >= 2).groupby('지역').sum().to_dict())
top = bm.loc[jump.sort_values(ascending=False).index[:10]].round(0)
top['최대/최소'] = jump.loc[top.index].round(2)
top
""")

md(r"""
### 2-3. SK: 특정 성·연령에 쏠린 소지역
소지역의 12개 성·연령 그룹 중 한 그룹의 비중이 비정상적으로 큰 곳입니다. 예를 들어 남성 20대가 몰린 곳은 군부대·대학·기숙사일 가능성이 있습니다 (6개월 합계 1,000명 이상 소지역만).
""")
code(r"""
bs = age.groupby(['지역', '소지역코드'])[ALL].sum()
bs = bs[bs.sum(axis=1) >= 1000]
shr = bs.div(bs.sum(axis=1), axis=0) * 100
shr.columns = [f'{COLMAP[c][0][0]}{COLMAP[c][1]}' for c in shr.columns]
print('소지역별 최대 그룹 비중 분포 (%)'); display(shr.max(axis=1).groupby('지역').describe(percentiles=[.5, .9, .99]).round(1))
out = pd.DataFrame({'6개월 합계': bs.sum(axis=1), '최대 그룹': shr.idxmax(axis=1), '최대 그룹 비중(%)': shr.max(axis=1)})
out.sort_values('최대 그룹 비중(%)', ascending=False).head(12).round(1)
""")

md(r"""
### 2-4. 카드: 객단가 이상값 (업종 내 robust z-score)
업종마다 log(객단가)의 중앙값·MAD로 z를 구해 |z| > 4인 행을 이상값으로 봅니다.
""")
code(r"""
q['log객단가'] = np.log10(q['금액'] / q['건수'])
g = q.groupby('업종')['log객단가']
med = g.transform('median'); mad = g.transform(lambda s: (s - s.median()).abs().median())
q['rz'] = (q['log객단가'] - med) / (1.4826 * mad.replace(0, np.nan))
q['이상값'] = q['rz'].abs() > 4
print(f"이상값 비율: {q['이상값'].mean() * 100:.2f}%  ({q['이상값'].sum():,}행)")
by_age = q.groupby(['연령', '지역'])['이상값'].mean().unstack().reindex(AGE9) * 100
by_ind = q.groupby('업종')['이상값'].agg(['mean', 'sum']).sort_values('mean', ascending=False).head(10)
by_ind['mean'] *= 100
display(pd.concat({'연령별 이상값 비율(%)': by_age}, axis=1).round(2))
display(by_ind.rename(columns={'mean': '이상값 비율(%)', 'sum': '행 수'}).round(2))
q.loc[q['rz'].abs().sort_values(ascending=False).index[:10],
      ['일자', '시간대', '지역', '업종', '성별', '연령', '금액', '건수', 'rz']].assign(객단가=lambda d: d['금액'] / d['건수']).round(1)
""")

md(r"""
### 2-5. 카드: 생애단계별 이상한 날
같은 요일 ±4주의 중앙값을 기준선으로 일별 건수 편차(%)를 구했습니다. 강건 z가 |3.5|를 넘는 날을 표시합니다.
""")
code(r"""
D = q.groupby(['일자', '지역', '생애단계'])['건수'].sum().unstack(['지역', '생애단계'])
base = D.groupby(D.index.dayofweek, group_keys=False).apply(lambda x: x.rolling(9, center=True, min_periods=4).median()).sort_index()
dev = (D / base - 1) * 100
rz = (dev - dev.median()) / (1.4826 * (dev - dev.median()).abs().median())
fig, axes = plt.subplots(2, 1, figsize=(12, 6), sharex=True)
for ax, r in zip(axes, MAIN):
    for s in STAGES:
        ax.plot(dev.index, dev[(r, s)], color=STAGE_C[s], lw=1.3, label=s)
    ax.axvspan(CHUSEOK[0], CHUSEOK[-1], color='#f0efec', zorder=0); ax.axhline(0, color=INK2, lw=.6)
    ax.set_title(f'{r}: 같은 요일 기준선 대비 일별 결제건수 편차'); pct_axis(ax)
axes[0].legend(ncol=4, fontsize=9, loc='lower left'); plt.tight_layout(); plt.show()
flag = (rz.abs() > 3.5)
lst = dev[flag.any(axis=1)].round(0)
lst.columns = [f'{r[3:]}·{s}' for r, s in lst.columns]
print(f'이상일 {len(lst)}일 (편차 %, 칸 = 지역·생애단계)'); lst
""")

md("### 2-6. 데이터1(법인 제외) ↔ 데이터2 성·연령별 정합성")
code(r"""
a = p1.groupby(['지역', '성별', '연령'])[['금액', '건수']].sum()
b = p2.groupby(['지역', '성별', '연령'])[['금액', '건수']].sum()
diff = ((a - b) / b * 100)
print('차이(%)의 절댓값 최대:', diff.abs().max().round(4).to_dict())
print('법인 행의 성별×연령 교차:'); pd.crosstab(s1['성별'], s1['연령'])
""")

# ------------------------------------------------------------------ 3. 연령
md(r"""
## 3. 연령별 분석

### 3-1. SK: 생애단계 구성 (지역 · 읍면동 유형)
""")
code(r"""
sk_long['구분'] = sk_long['지역'].str[3:] + ' ' + np.where(sk_long['유형'] == '동', '동', '읍·면')
st = sk_long.groupby(['구분', '생애단계'])['인구'].sum().unstack()[STAGES]
st = st.div(st.sum(axis=1), axis=0) * 100
colors = {'강남구 동': C['서울 강남구'], '춘천시 동': C['강원 춘천시'], '춘천시 읍·면': '#f5b394'}
fig, ax = plt.subplots(figsize=(9, 3.4)); x = np.arange(4); w = .27
for i, k in enumerate(st.index):
    ax.bar(x + (i - 1) * w, st.loc[k], width=w - .03, color=colors[k], label=k)
ax.set_xticks(x, STAGES); pct_axis(ax); ax.legend(); ax.set_title('생애단계별 유동인구 비중'); plt.show()
st.round(1)
""")

md("### 3-2. SK: 읍면동 × 연령 구성 (6개월 합계 기준 비중 %)")
code(r"""
dz = sk_long.groupby(['지역', '읍면동', '유형', '연령'])['인구'].sum().unstack()[AGE6]
dz = dz.div(dz.sum(axis=1), axis=0) * 100
fig, axes = plt.subplots(1, 2, figsize=(12, 8.5))
for ax, r in zip(axes, MAIN):
    sub = dz.loc[r].sort_values('60대+', ascending=False)
    sub.index = [f'{c}({t})' for c, t in sub.index]
    im = heat(ax, sub, vmin=0, vmax=dz.values.max(), fs=7.5); ax.set_title(r + ' (60대+ 비중 순)')
fig.colorbar(im, ax=axes, shrink=.5, label='%'); plt.show()
""")

md("### 3-3. SK: 연령대별 월별 유동인구 지수 (7월 = 100)")
code(r"""
mm = sk_long.groupby(['지역', '연령', '기준년월'])['인구'].sum().unstack()
idx = mm.div(mm['202507'], axis=0) * 100
fig, axes = plt.subplots(1, 2, figsize=(12, 3.6), sharey=True)
for ax, r in zip(axes, MAIN):
    for a, col in zip(AGE6, RAMP6):
        ax.plot(idx.columns, idx.loc[(r, a)], color=col, marker='o', ms=4, label=a)
    ax.axhline(100, color=INK2, lw=.6); ax.set_title(r)
axes[0].legend(ncol=3, fontsize=9); plt.tight_layout(); plt.show()
idx.round(1)
""")

md("### 3-4. SK: 읍면동 × 연령대별 겨울철 변화 (12월 vs 7~8월 평균, %)")
code(r"""
w = sk_long.groupby(['지역', '읍면동', '유형', '연령', '기준년월'])['인구'].sum().unstack()
wchg = ((w['202512'] / w[['202507', '202508']].mean(axis=1) - 1) * 100).unstack('연령')[AGE6]
fig, axes = plt.subplots(1, 2, figsize=(12, 8.5))
for ax, r in zip(axes, MAIN):
    sub = wchg.loc[r].sort_values('60대+')
    sub.index = [f'{c}({t})' for c, t in sub.index]
    im = heat(ax, sub, cmap=DIV, center=0, clip=40, fmt='{:.0f}', fs=7.5); ax.set_title(r + ' (파랑=감소 · 빨강=증가, ±40%에서 포화)')
fig.colorbar(im, ax=axes, shrink=.5, label='%'); plt.show()
reg = sk_long.groupby(['지역', '연령', '기준년월'])['인구'].sum().unstack()
print('지역 전체 겨울철 변화(%)'); ((reg['202512'] / reg[['202507', '202508']].mean(axis=1) - 1) * 100).unstack().round(1)[AGE6]
""")

md("### 3-5. 카드: 연령대별 소비 규모 · 객단가 (일상 소비)")
code(r"""
ga = q.groupby(['지역', '연령'])[['금액', '건수']].sum()
ga['건수 비중(%)'] = ga['건수'] / ga.groupby('지역')['건수'].transform('sum') * 100
ga['금액 비중(%)'] = ga['금액'] / ga.groupby('지역')['금액'].transform('sum') * 100
ga['객단가(원)'] = ga['금액'] / ga['건수']
fig, axes = plt.subplots(1, 2, figsize=(12, 3.4))
x = np.arange(9); w = .4
for i, r in enumerate(MAIN):
    t = ga.loc[r].reindex(AGE9)
    axes[0].bar(x + (i - .5) * w, t['건수 비중(%)'], width=w - .03, color=C[r], label=r)
    axes[1].bar(x + (i - .5) * w, t['객단가(원)'] / 1e3, width=w - .03, color=C[r], label=r)
for ax, tt in zip(axes, ['연령대별 결제건수 비중(%)', '연령대별 객단가(천원)']):
    ax.set_xticks(x, AGE9); ax.set_title(tt)
axes[0].legend(); plt.tight_layout(); plt.show()
ga[['건수 비중(%)', '금액 비중(%)', '객단가(원)']].unstack(0).reindex(AGE9).round(1)
""")

md("### 3-6. 카드: 연령대 × 업종그룹 구성 (건수 비중 %)")
code(r"""
gg = q.groupby(['지역', '연령', '업종그룹'])['건수'].sum()
sh_g = gg / gg.groupby(['지역', '연령']).transform('sum') * 100
fig, axes = plt.subplots(2, 1, figsize=(13, 8))
for ax, r in zip(axes, MAIN):
    t = sh_g.loc[r].unstack().reindex(index=AGE9, columns=GORDER)
    im = heat(ax, t, fs=8, rot=30); ax.set_title(r)
    fig.colorbar(im, ax=ax, shrink=.9, label='%')
plt.tight_layout(); plt.show()
""")

md(r"""
### 3-7. 카드: 생애단계별 '특화 업종'
특화지수 = (해당 생애단계의 업종 건수 비중) ÷ (전체의 업종 건수 비중) × 100 입니다. 비중 0.3% 이상인 업종만 대상으로 상위 8개를 보여 줍니다.
""")
code(r"""
gi = q.groupby(['지역', '생애단계', '업종'])['건수'].sum()
s_stage = gi / gi.groupby(['지역', '생애단계']).transform('sum') * 100
s_all = q.groupby(['지역', '업종'])['건수'].sum(); s_all = s_all / s_all.groupby('지역').transform('sum') * 100
oi = s_stage / s_all.reindex(s_stage.index.droplevel('생애단계')).values * 100
res = {}
for r in MAIN:
    for s in STAGES:
        t = pd.DataFrame({'비중%': s_stage.loc[(r, s)], '특화지수': oi.loc[(r, s)]})
        t = t[t['비중%'] >= .3].nlargest(8, '특화지수').reset_index()
        res[(r[3:], s)] = t.apply(lambda row: f"{row['업종']} ({row['특화지수']:.0f})", axis=1)
pd.DataFrame(res)
""")

md("### 3-8. 카드: 연령대별 시간대 분포 (건수 비중 %)")
code(r"""
gt_ = q.groupby(['지역', '연령', '시간대'])['건수'].sum()
sh_t = (gt_ / gt_.groupby(['지역', '연령']).transform('sum') * 100)
fig, axes = plt.subplots(1, 2, figsize=(11, 3.9))
for ax, r in zip(axes, MAIN):
    t = sh_t.loc[r].unstack().reindex(AGE9)
    t.columns = ['00~05시', '06~11시', '12~17시', '18~23시']
    heat(ax, t, vmin=0, vmax=60); ax.set_title(r)
plt.tight_layout(); plt.show()
""")

md("### 3-9. 카드: 연령대별 요일 패턴 (공휴일 제외, 평일 평균 = 100)")
code(r"""
dd = q[q['일유형'] != '공휴일'].groupby(['지역', '연령6', '일자'])['건수'].sum().reset_index()
dd['요일'] = dd['일자'].dt.dayofweek
dw = dd.groupby(['지역', '연령6', '요일'])['건수'].mean().unstack()
dw = dw.div(dw[[0, 1, 2, 3, 4]].mean(axis=1), axis=0) * 100
dw.columns = list('월화수목금토일')
fig, axes = plt.subplots(1, 2, figsize=(11, 3.2))
for ax, r in zip(axes, MAIN):
    heat(ax, dw.loc[r].reindex(AGE6), cmap=DIV, center=100, clip=50, fmt='{:.0f}'); ax.set_title(r)
plt.tight_layout(); plt.show()
""")

md(r"""
### 3-10. 카드: 추석 연휴 효과 (연령 × 성별)
연휴(10/3~10/9) 일평균 건수를 전후 3주(9/12~10/2, 10/10~10/30) 일평균과 비교한 변화율입니다.
""")
code(r"""
REF = (q['일자'].between('2025-09-12', '2025-10-02')) | (q['일자'].between('2025-10-10', '2025-10-30'))
HOLM = q['일자'].isin(CHUSEOK)
def holiday_effect(keys):
    h = q[HOLM].groupby(keys + ['일자'])['건수'].sum().groupby(keys).mean()
    b = q[REF].groupby(keys + ['일자'])['건수'].sum().groupby(keys).mean()
    return (h / b - 1) * 100
he = holiday_effect(['지역', '연령6', '성별']).unstack()
fig, axes = plt.subplots(1, 2, figsize=(12, 3.4), sharey=True)
for ax, r in zip(axes, MAIN):
    sex_bars(ax, he.loc[r].reindex(AGE6), r, AGE6); ax.axhline(0, color=INK2, lw=.8)
    ax.set_title(f'{r}: 추석 연휴 결제건수 변화'); pct_axis(ax); ax.legend(fontsize=9)
plt.tight_layout(); plt.show()
hg = holiday_effect(['지역', '연령6', '업종그룹']).unstack()[[g for g in GORDER if g not in ('교육·육아', '공과금·금융·기타서비스')]]
fig, axes = plt.subplots(2, 1, figsize=(13, 5.6))
for ax, r in zip(axes, MAIN):
    heat(ax, hg.loc[r].reindex(AGE6), cmap=DIV, center=0, clip=60, fmt='{:.0f}', rot=25); ax.set_title(f'{r}: 업종그룹별 추석 연휴 변화(%) · 파랑=감소 · 빨강=증가')
plt.tight_layout(); plt.show()
""")

md("### 3-11. 카드: 연령대별 월별 추이 (건수, 7월 = 100)")
code(r"""
fig, axes = plt.subplots(2, 2, figsize=(12, 6.4), sharey='row')
for j, (lab, mask) in enumerate([('일상 소비 전체', q['일상소비']), ('외부·사회활동형 업종', q['업종그룹'].isin(OUTDOOR))]):
    m = q[mask].groupby(['지역', '연령6', '기준년월'])['건수'].sum().unstack()
    m = m.div(m['202507'], axis=0) * 100
    for i, r in enumerate(MAIN):
        ax = axes[j, i]
        for a, col in zip(AGE6, RAMP6):
            ax.plot(m.columns, m.loc[(r, a)], color=col, marker='o', ms=4, label=a)
        ax.axhline(100, color=INK2, lw=.6); ax.set_title(f'{r} · {lab}'); ax.tick_params(axis='x', labelsize=8)
axes[0, 0].legend(ncol=3, fontsize=8); plt.tight_layout(); plt.show()
""")

md(r"""
### 3-12. 카드(데이터2): 연령대별 관내 거주 고객 비중
관내 = 가맹점과 같은 시도에 사는 고객(강남: 서울, 춘천: 강원)이며, '정보없음'은 뺐습니다. 관내 비중이 높을수록 이동 반경이 좁은 생활권 소비로 볼 수 있습니다.
""")
code(r"""
HOME = {'서울 강남구': '서울', '강원 춘천시': '강원'}
k2 = p2[p2['거주지'] != '정보없음']
g = k2.assign(관내=k2['거주지'] == k2['지역'].map(HOME)).groupby(['연령', '지역', '성별', '관내'])['건수'].sum()
local = (g.xs(True, level='관내') / g.groupby(['연령', '지역', '성별']).sum() * 100).unstack(['지역', '성별']).reindex(AGE9)
fig, ax = plt.subplots(figsize=(9, 3.5)); rs_lines(ax, local)
ax.set_title('연령대별 관내 거주 고객 건수 비중'); pct_axis(ax); ax.legend(ncol=4, fontsize=8.5); plt.show()
local.round(1)
""")

# ------------------------------------------------------------------ 4. 성별
md(r"""
## 4. 성별 분석

### 4-1. SK: 연령대별 성비 (여성 = 100)
""")
code(r"""
sr = sk_long.groupby(['지역', '연령', '성별'])['인구'].sum().unstack()
sr = (sr['남성'] / sr['여성'] * 100).unstack().reindex(columns=AGE6)
fig, ax = plt.subplots(figsize=(8, 3.3)); x = np.arange(6); w = .4
for i, r in enumerate(MAIN):
    ax.bar(x + (i - .5) * w, sr.loc[r], width=w - .03, color=C[r], label=r)
ax.axhline(100, color=INK2, lw=.8); ax.set_xticks(x, AGE6); ax.set_ylim(60, None)
ax.set_title('유동인구 성비 (여성=100)'); ax.legend(); plt.show()
srd = sk_long.groupby(['지역', '읍면동', '유형', '연령', '성별'])['인구'].sum().unstack()
srd = (srd['남성'] / srd['여성'] * 100).unstack()[AGE6]
fig, axes = plt.subplots(1, 2, figsize=(12, 8.5))
for ax, r in zip(axes, MAIN):
    sub = srd.loc[r].sort_values('20대', ascending=False)
    sub.index = [f'{c}({t})' for c, t in sub.index]
    im = heat(ax, sub, cmap=DIV, center=100, clip=60, fmt='{:.0f}', fs=7.5); ax.set_title(f'{r} 읍면동 성비 (20대 성비 순)')
fig.colorbar(im, ax=axes, shrink=.5); plt.show()
""")

md("### 4-2. 카드: 연령대별 성별 결제건수비 · 객단가비 (남 ÷ 여 × 100)")
code(r"""
gs = q.groupby(['지역', '연령', '성별'])[['금액', '건수']].sum()
gs['객단가'] = gs['금액'] / gs['건수']
ratio = pd.DataFrame({'건수 성비': gs['건수'].xs('남성', level='성별') / gs['건수'].xs('여성', level='성별') * 100,
                      '객단가 성비': gs['객단가'].xs('남성', level='성별') / gs['객단가'].xs('여성', level='성별') * 100})
fig, axes = plt.subplots(1, 2, figsize=(12, 3.3), sharey=True)
for ax, col in zip(axes, ratio.columns):
    t = ratio[col].unstack(0).reindex(AGE9)
    for r in MAIN: ax.plot(AGE9, t[r], color=C[r], marker='o', ms=5, label=r)
    ax.axhline(100, color=INK2, lw=.8); ax.set_title(f'{col} (여성=100)')
axes[0].legend(); plt.tight_layout(); plt.show()
ratio.unstack(0).reindex(AGE9).round(0)
""")

md("### 4-3. 카드: 성별 업종그룹 비중 차이 (남성 비중 − 여성 비중, %p)")
code(r"""
gsx = q.groupby(['지역', '성별', '연령', '업종그룹'])['건수'].sum()
shx = gsx / gsx.groupby(['지역', '성별', '연령']).transform('sum') * 100
dsx = (shx.xs('남성', level='성별') - shx.xs('여성', level='성별'))
fig, axes = plt.subplots(2, 1, figsize=(13, 7.5))
for ax, r in zip(axes, MAIN):
    t = dsx.loc[r].unstack().reindex(index=AGE9, columns=GORDER)
    im = heat(ax, t, cmap=DIV, center=0, clip=12, fmt='{:+.1f}', rot=30); ax.set_title(f'{r} (빨강 = 남성이 더 많이 씀)')
    fig.colorbar(im, ax=ax, shrink=.9, label='%p')
plt.tight_layout(); plt.show()
""")

md(r"""
### 4-4. 카드: 고립 관련 후보 지표 (연령 × 성별 × 지역)
모든 값은 해당 그룹 일상 소비 건수 중 비중(%)입니다.
- **외부·사회활동형**: 외식·여가문화·스포츠·음주유흥·여행교통 → 낮으면 대외 활동이 적다는 신호 후보
- **편의점**: 1인 가구 소비 패턴 후보 · **음주·유흥**: 유흥업소+주류판매 · **심야**: 00~05시
- **의료**: 건강 취약 후보 · **주말·공휴일**: 주말 활동성
""")
code(r"""
base = q.groupby(['지역', '성별', '연령'])['건수'].sum()
def share(mask):
    return q[mask].groupby(['지역', '성별', '연령'])['건수'].sum().reindex(base.index).fillna(0) / base * 100
IND = pd.DataFrame({
    '외부·사회활동형': share(q['업종그룹'].isin(OUTDOOR)), '편의점': share(q['업종그룹'] == '편의점'),
    '음주·유흥': share(q['업종그룹'] == '음주·유흥'), '심야(00~05시)': share(q['시간대'] == '00_05'),
    '의료': share(q['업종그룹'] == '의료'), '주말·공휴일': share(q['일유형'] != '평일')})
fig, axes = plt.subplots(2, 3, figsize=(14, 7))
for ax, col in zip(axes.flat, IND.columns):
    rs_lines(ax, IND[col].unstack(['지역', '성별']).reindex(AGE9), labels=(col == IND.columns[0]))
    ax.set_title(col); pct_axis(ax, dec=2 if IND[col].max() < 1 else 0); ax.tick_params(axis='x', labelsize=8)
plt.tight_layout(rect=[0, 0, 1, .95]); fig.legend(loc='upper center', ncol=4, bbox_to_anchor=(.5, 1.0)); plt.show()
IND.unstack(['지역', '성별']).reindex(AGE9).round(1)
""")

md("### 4-5. 카드: 겨울철 변화 (12월 vs 7~8월, 건수 %): 연령 × 성별")
code(r"""
def winter(mask):
    m = q[mask].groupby(['지역', '성별', '연령', '기준년월'])['건수'].sum().unstack()
    return (m['202512'] / m[['202507', '202508']].mean(axis=1) - 1) * 100
WIN = pd.DataFrame({'일상 소비 전체': winter(q['일상소비']), '외부·사회활동형': winter(q['업종그룹'].isin(OUTDOOR)),
                    '편의점': winter(q['업종그룹'] == '편의점'), '의료': winter(q['업종그룹'] == '의료')})
fig, axes = plt.subplots(1, 4, figsize=(15, 3.6), sharey=True)
for ax, col in zip(axes, WIN.columns):
    rs_lines(ax, WIN[col].unstack(['지역', '성별']).reindex(AGE9[:8]), labels=(col == WIN.columns[0]))
    ax.axhline(0, color=INK2, lw=.6); ax.set_title(col); pct_axis(ax); ax.tick_params(axis='x', labelsize=8)
plt.tight_layout(rect=[0, 0, 1, .9]); fig.legend(loc='upper center', ncol=4, bbox_to_anchor=(.5, 1.0)); plt.show()
print('90대는 표본이 작아 그래프에서 제외')
WIN.unstack(['지역', '성별']).reindex(AGE9).round(1)
""")

# ------------------------------------------------------------------ 5. 지역
md(r"""
## 5. 지역별 분석

### 5-1. 읍면동 프로파일 (47개)
""")
code(r"""
lsp = sk_long.groupby(['지역', '읍면동', '유형', '생애단계'])['인구'].sum().unstack()[STAGES]
P = lsp.div(lsp.sum(axis=1), axis=0) * 100
P.columns = [f'{c} 비중' for c in P.columns]
P.insert(0, '월평균 유동인구', sk_long.groupby(['지역', '읍면동', '유형'])['인구'].sum() / 6)
sx = sk_long.groupby(['지역', '읍면동', '유형', '성별'])['인구'].sum().unstack()
P['남성 비중'] = sx['남성'] / sx.sum(axis=1) * 100
gt2 = tm.groupby(['지역', '읍면동', '유형'])[TM].sum()
P['주간/야간 비'] = (gt2[TM[9:18]].sum(axis=1) / 9) / (gt2[TM[0:6]].sum(axis=1) / 6)
gw2 = wk.groupby(['지역', '읍면동', '유형'])[WD].sum()
P['주말/평일 비'] = gw2[WD[5:]].mean(axis=1) / gw2[WD[:5]].mean(axis=1)
wst = sk_long.groupby(['지역', '읍면동', '유형', '생애단계', '기준년월'])['인구'].sum().unstack()
wch = (wst['202512'] / wst[['202507', '202508']].mean(axis=1) - 1) * 100
for s in STAGES[1:]:
    P[f'{s.split("(")[0]} 겨울변화%'] = wch.xs(s, level='생애단계')
P.round(1).style.format('{:,.1f}').background_gradient(cmap=SEQ, subset=[c for c in P.columns if '비중' in c]) \
    .background_gradient(cmap=DIV, vmin=-40, vmax=40, subset=[c for c in P.columns if '겨울' in c])
""")

md("### 5-2. 읍면동 유형별 시간대 · 요일 패턴 (SK)")
code(r"""
KIND_C = {'강남구 동': C['서울 강남구'], '춘천시 동': C['강원 춘천시'], '춘천시 읍·면': '#f5b394'}
for d in (tm, wk):
    d['구분'] = d['지역'].str[3:] + ' ' + np.where(d['유형'] == '동', '동', '읍·면')
ht = tm.groupby('구분')[TM].sum(); ht = ht.div(ht.sum(axis=1), axis=0) * 100
wd = wk.groupby('구분')[WD].sum(); wd = wd.div(wd[WD[:5]].mean(axis=1), axis=0) * 100
fig, axes = plt.subplots(1, 2, figsize=(13, 3.6), gridspec_kw={'width_ratios': [3, 2]})
for k in ht.index:
    axes[0].plot(range(24), ht.loc[k], color=KIND_C[k], marker='o', ms=3.5, label=k)
axes[0].set_xticks(range(0, 24, 2)); pct_axis(axes[0], dec=0); axes[0].set_title('시간대별 유동인구 비중'); axes[0].legend(fontsize=9)
x = np.arange(7); w = .27
for i, k in enumerate(wd.index):
    axes[1].bar(x + (i - 1) * w, wd.loc[k], width=w - .03, color=KIND_C[k], label=k)
axes[1].set_xticks(x, list('월화수목금토일')); axes[1].axhline(100, color=INK2, lw=.8); axes[1].set_ylim(50, 125)
axes[1].set_title('요일 지수 (평일 평균=100)'); plt.tight_layout(); plt.show()
wd.round(1).set_axis(list('월화수목금토일'), axis=1)
""")

md("### 5-3. 지도: 격자별 생애단계 비중 (6개월 합계, 평균 유동인구 5명 이상 격자)")
code(r"""
cs = age.groupby(['지역', 'X좌표', 'Y좌표'])[ALL].sum()
cs = cs[cs.sum(axis=1) >= 30]
stage_cols = {s: [c for c in ALL if LS[COLMAP[c][1]] == s] for s in STAGES[1:]}
def grid_image(d, val):
    xs, ys = d.index.get_level_values('X좌표'), d.index.get_level_values('Y좌표')
    gx = np.round((xs - xs.min()) / 50).astype(int); gy = np.round((ys - ys.min()) / 50).astype(int)
    img = np.full((gy.max() + 1, gx.max() + 1), np.nan); img[gy, gx] = val
    return img, (xs.min() - 25, xs.max() + 25, ys.min() - 25, ys.max() + 25)
fig, axes = plt.subplots(2, 3, figsize=(15, 10))
for i, r in enumerate(MAIN):
    d = cs.loc[r]; tot = d.sum(axis=1)
    for j, (s, cols) in enumerate(stage_cols.items()):
        ax = axes[i, j]; v = d[cols].sum(axis=1) / tot * 100
        img, ext = grid_image(d, v.values)
        im = ax.imshow(img, origin='lower', extent=ext, cmap=SEQ, vmin=np.nanpercentile(v, 2), vmax=np.nanpercentile(v, 98))
        ax.set_title(f'{r} · {s}' + (' (시내 중심 12km)' if r == '강원 춘천시' else '')); ax.set_aspect('equal')
        if r == '강원 춘천시':   # 시 전체는 너무 넓어 시내 중심부만 확대
            xs_, ys_ = d.index.get_level_values('X좌표'), d.index.get_level_values('Y좌표')
            cx, cy = np.average(xs_, weights=tot), np.average(ys_, weights=tot)
            ax.set_xlim(cx - 6000, cx + 6000); ax.set_ylim(cy - 6000, cy + 6000)
        ax.grid(False); ax.set_xticks([]); ax.set_yticks([])
        for sp in ax.spines.values(): sp.set_visible(False)
        cb = fig.colorbar(im, ax=ax, shrink=.6); cb.ax.yaxis.set_major_formatter(mtick.PercentFormatter(decimals=0))
plt.tight_layout(); plt.show()
""")

md("### 5-4. 읍면동 특성 간 상관 (스피어만)")
code(r"""
fig, axes = plt.subplots(1, 2, figsize=(15, 6.2))
for ax, r in zip(axes, MAIN):
    cm = P.loc[r].corr(method='spearman')
    heat(ax, cm, cmap=DIV, center=0, clip=1, fmt='{:.2f}', fs=7, rot=40); ax.set_title(f'{r} (n={len(P.loc[r])})')
plt.tight_layout(); plt.show()
""")

md("### 5-5. 카드: 지역 차이 (강남 비중 − 춘천 비중, %p) 연령 × 업종그룹")
code(r"""
dreg = (sh_g.xs('서울 강남구', level='지역') - sh_g.xs('강원 춘천시', level='지역')).unstack().reindex(index=AGE9, columns=GORDER)
fig, ax = plt.subplots(figsize=(13, 4.2))
im = heat(ax, dreg, cmap=DIV, center=0, clip=15, fmt='{:+.1f}', rot=30); ax.set_title('파랑 = 춘천이 더 높음 · 빨강 = 강남이 더 높음')
fig.colorbar(im, ax=ax, shrink=.9, label='%p'); plt.show()
""")

md("### 5-6. 지역 핵심 지표 비교")
code(r"""
kpi = pd.DataFrame({
    '셀 일평균 유동인구(중앙값)': age.groupby('지역')['총'].median(),
    '청년 유동 비중(%)': sk_long[sk_long['생애단계'] == STAGES[1]].groupby('지역')['인구'].sum() / sk_long.groupby('지역')['인구'].sum() * 100,
    '중년 유동 비중(%)': sk_long[sk_long['생애단계'] == STAGES[2]].groupby('지역')['인구'].sum() / sk_long.groupby('지역')['인구'].sum() * 100,
    '노년 유동 비중(%)': sk_long[sk_long['생애단계'] == STAGES[3]].groupby('지역')['인구'].sum() / sk_long.groupby('지역')['인구'].sum() * 100,
    '주간/야간 비': tm.groupby('지역')[TM[9:18]].sum().sum(axis=1) / 9 / (tm.groupby('지역')[TM[0:6]].sum().sum(axis=1) / 6),
    '주말/평일 비': wk.groupby('지역')[WD[5:]].sum().mean(axis=1) / wk.groupby('지역')[WD[:5]].sum().mean(axis=1),
    '카드 객단가(원)': q.groupby('지역')['금액'].sum() / q.groupby('지역')['건수'].sum(),
    '외부·사회활동형(%)': q[q['업종그룹'].isin(OUTDOOR)].groupby('지역')['건수'].sum() / q.groupby('지역')['건수'].sum() * 100,
    '편의점(%)': q[q['업종그룹'] == '편의점'].groupby('지역')['건수'].sum() / q.groupby('지역')['건수'].sum() * 100,
    '심야(%)': q[q['시간대'] == '00_05'].groupby('지역')['건수'].sum() / q.groupby('지역')['건수'].sum() * 100,
    '의료(%)': q[q['업종그룹'] == '의료'].groupby('지역')['건수'].sum() / q.groupby('지역')['건수'].sum() * 100,
}).T[MAIN]
kpi.round(2)
""")

# ------------------------------------------------------------------ 6. 결합
md(r"""
## 6. 결합: 유동인구 대비 소비 참여도

**참여도** = (그룹의 카드 결제건수 비중) ÷ (그룹의 유동인구 비중)입니다. 1보다 작으면 그 지역에 머무는 정도에 비해 카드 소비 활동이 적다는 뜻입니다.
고령층은 현금 사용이 많고 신한카드 점유율도 연령마다 다를 수 있으므로, 절대 수준보다는 **지역 간·시점 간 비교**에 쓰는 것이 안전합니다.
""")
code(r"""
pop = sk_long.groupby(['지역', '성별', '연령'])['인구'].sum(); pop_sh = pop / pop.groupby('지역').transform('sum') * 100
cd = q.groupby(['지역', '성별', '연령6'])['건수'].sum(); cd.index.names = ['지역', '성별', '연령']
cd_sh = cd / cd.groupby('지역').transform('sum') * 100
part = (cd_sh / pop_sh).unstack('성별')
fig, axes = plt.subplots(1, 2, figsize=(12, 3.5), sharey=True)
for ax, r in zip(axes, MAIN):
    sex_bars(ax, part.loc[r].reindex(AGE6), r, AGE6); ax.axhline(1, color=INK2, lw=.8)
    ax.set_title(f'{r}: 소비 참여도 (1 = 유동 비중만큼 소비)'); ax.legend(fontsize=9)
plt.tight_layout(); plt.show()
display(pd.concat({'유동 비중%': pop_sh.unstack('성별'), '카드건수 비중%': cd_sh.unstack('성별'), '참여도': part}, axis=1)
        .reindex(AGE6, level='연령').round(2))

popm = sk_long.groupby(['지역', '생애단계', '기준년월'])['인구'].sum(); popm = popm / popm.groupby(['지역', '기준년월']).transform('sum')
cdm = q.groupby(['지역', '생애단계', '기준년월'])['건수'].sum(); cdm = cdm / cdm.groupby(['지역', '기준년월']).transform('sum')
pm = (cdm / popm).unstack('기준년월')
fig, axes = plt.subplots(1, 2, figsize=(12, 3.3), sharey=True)
for ax, r in zip(axes, MAIN):
    for s in STAGES: ax.plot(pm.columns, pm.loc[(r, s)], color=STAGE_C[s], marker='o', ms=4, label=s)
    ax.axhline(1, color=INK2, lw=.6); ax.set_title(f'{r}: 월별 소비 참여도')
axes[0].legend(fontsize=8.5, ncol=2); plt.tight_layout(); plt.show()
pm.round(3)
""")

md("## 7. 관찰 요약 · 신호 후보 · 한계")
md(SUMMARY.read_text(encoding="utf-8") if SUMMARY.exists() else "_(실행 결과를 바탕으로 작성 예정)_")

nb = nbf.v4.new_notebook(cells=cells, metadata={
    "kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"},
    "language_info": {"name": "python"}})
flow.annotate(cells, flow.FLOW2, flow.TITLE2)
nb.cells = cells
nbf.write(nb, OUT)
print("written", OUT, len(cells), "cells")
