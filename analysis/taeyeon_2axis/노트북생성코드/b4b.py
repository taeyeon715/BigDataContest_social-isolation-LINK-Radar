from pathlib import Path
import nbformat as nbf
import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent)); import flow

OUT = Path(__file__).resolve().parent.parent / '부록B_지수형_첫검증.ipynb'
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md(r"""
# 04b
**무엇이 다른가**

| | 04 학습형 | **04b 지수형** |
|---|---|---|
| 가중치를 정하는 방법 | 강남 SKT 정답(서울 관심집단)에 맞춰 **학습** | 이론으로 **미리 정함** (정답 미사용) |
| 쓰는 SK 지표 | 6개 + 구조(주민등록) | 2개: '동네 체류도'(유동/거주 비), '겨울 체류 유지'(겨울 변화) |
| 지역 처리 | 강남에서 학습 → 춘천에 옮겨 적용 | 두 지역에 **같은 규칙** (지역 안 순위) |
| 검증 | 강남 SKT 정답 하나 | **출처가 다른 여러 지표**로 지역별 검증 (SKT는 참고용) |
""")

code(r"""
import warnings, glob, os; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np, pandas as pd, openpyxl
import matplotlib.pyplot as plt, matplotlib.ticker as mtick
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from scipy.stats import spearmanr
plt.rcParams.update({'font.family': 'Malgun Gothic', 'axes.unicode_minus': False, 'figure.dpi': 110,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'axes.axisbelow': True,
    'grid.color': '#e6e5e1', 'grid.linewidth': .6, 'axes.edgecolor': '#b5b4ae', 'axes.labelcolor': '#52514e',
    'xtick.color': '#52514e', 'ytick.color': '#52514e', 'axes.titlesize': 11.5, 'axes.titleweight': 'bold',
    'axes.titlelocation': 'left', 'legend.frameon': False})
pd.set_option('display.max_columns', 40); pd.set_option('display.width', 220); pd.set_option('display.float_format', '{:,.3f}'.format)
EXT, OUTD = Path('external'), Path('outputs')
MAIN = ['서울 강남구', '강원 춘천시']; C_ = {'서울 강남구': '#2a78d6', '강원 춘천시': '#eb6834'}
INK, INK2, GRAY = '#0b0b0b', '#52514e', '#b5b4ae'
DIV = LinearSegmentedColormap.from_list('div', ['#1c5cab', '#86b6ef', '#f0efec', '#f2a9a8', '#c43a39'])
U = pd.read_csv(OUTD / '위험도_강남_춘천_동x성x연령.csv', dtype={'SGIS코드': str, '행정기관코드': str})   # 04의 결과(학습형 점수·SK 지표 포함)
U['cell'] = U['성별'] + '_' + U['연령5']
print('분석 단위:', U.groupby('지역').size().to_dict())
""")

# ---------------------------------------------------------------- 1
md("## 1. 지수 구성 — 정답 없이 이론으로 정한 지표와 방향")
md(r"""
| 지표 | 데이터 | 방향 | 근거 |
|---|---|---|---|
| **동네 체류도** = 그 집단의 유동인구 ÷ 거주 인구 | SK | 높을수록 위험 | 고립·은둔의 정의('제한된 장소에 머무름'), 판단도구 '외출·교류 없음' |
| **겨울 체류 유지** = 12월 ÷ 7~8월 유동인구 | SK | 높을수록 위험 (겨울에도 동네에 머묾 = 외출 감소) | 계절성 고립, 02의 겨울 분석 |
| 외부활동 소비 비중 | 신한 | 낮을수록 위험 | 사회적 활동 감소 (02) |
| 소비 참여도 = 카드 비중 ÷ 유동 비중 | 신한 | 낮을수록 위험 | 머무는 만큼 활동하지 않음 (02) |

**뺀 지표와 이유**: 야간 유동·주간/야간 비·주말/평일 비·집단 유동 비중은 이론상 위험 방향을 정할 수 없어 제외했습니다(예: 야간 유동이 많은 것은 '집에 머묾'일 수도, '유흥가'일 수도 있음).

**합치는 방법**: SK 두 지표는 지역 × 성 × 연령 안 백분위의 평균(동일 가중) → 위험 배수 = exp(0.6 × (평균 − 0.5)), 범위 약 0.74~1.35배(04 학습형과 비슷한 폭). 카드는 04와 같은 작은 가중치(±4%). 기본 위험 B(공식 고립률)는 04와 같습니다.
""")
code(r"""
for f in ['유동/거주 비', '겨울 변화']:
    U[f + '_p'] = U.groupby(['지역', '성별', '연령5'])[f].rank(pct=True)
U['H_SK'] = U[['유동/거주 비_p', '겨울 변화_p']].mean(axis=1)
U['R'] = np.exp(0.6 * (U['H_SK'] - 0.5))                       # SK 행동 위험 배수 (정답 미사용)
U['지수형 점수'] = U['B 기본 고립률(%)'] * U['R'] * U['C']        # C: 04와 같은 카드 집단 배수
print('SK 행동 위험 배수 범위:', U['R'].min().round(2), '~', U['R'].max().round(2))
U.groupby('지역')[['R', 'SA']].describe().loc[:, (slice(None), ['mean', 'min', 'max'])].round(3)
""")

# ---------------------------------------------------------------- 2
md("## 2. 독립 검증 지표 모으기 (SKT와 출처가 다른 행정 통계)")
code(r"""
S = pd.read_csv(EXT / 'mois/읍면동_구조지표_강남_춘천.csv', dtype={'SGIS코드': str}).set_index('SGIS코드')
def table(fname_key, sheet, cols, names):
    f = [x for x in glob.glob(str(EXT / 'chuncheon/*.xlsx')) if fname_key in os.path.basename(x)][0]
    out = {}
    for r in openpyxl.load_workbook(f, read_only=True, data_only=True)[sheet].iter_rows(values_only=True):
        v = [c for c in r if c is not None]
        if v and isinstance(v[0], str) and v[0].strip() in names: out[v[0].strip()] = [v[i] for i in cols]
    return pd.DataFrame(out).T
ch_names = set(S.loc[S['지역'] == '강원 춘천시', '읍면동명'])
CH = (table('11_', '29.기초연금수급자수', [1, 4], ch_names).set_axis(['65세+', '기초연금수급자'], axis=1)
      .join(table('11_', '34.장애인등록', [1], ch_names).set_axis(['장애인'], axis=1))
      .join(table('03_', '02.읍면동별 세대 및 인구', [2], ch_names).set_axis(['등록인구'], axis=1))
      .join(table('03_', '06.읍면동별인구인동', [1, 4], ch_names).set_axis(['전입', '전출'], axis=1)).astype(float))
CH['기초연금 수급률(%)'] = CH['기초연금수급자'] / CH['65세+'] * 100
CH['장애인 비율(%)'] = CH['장애인'] / CH['등록인구'] * 100
CH['인구 이동률(%)'] = (CH['전입'] + CH['전출']) / CH['등록인구'] * 100
CH.index = CH.index.map(dict(zip(S.loc[S['지역'] == '강원 춘천시', '읍면동명'], S.loc[S['지역'] == '강원 춘천시'].index)))
# 강남 SKT 고립 지표 (참고용, 같은 출처) — 동 단위 성·연령 표준화 평균
L = []
for m in range(7, 13):
    d = pd.read_excel(EXT / f'seoul_interest/interest_2025_{m:02d}.xlsx'); L.append(d.loc[:, ~d.columns.astype(str).str.startswith('Unnamed')])
si = pd.concat(L); GI = '외출-커뮤니케이션이 모두 적은 집단(전체)'
si['연령5'] = si['연령대'].map(lambda a: '60대+' if a >= 60 else f'{a // 10 * 10}대'); si['성별'] = si['성별'].map({1: '남성', 2: '여성'})
T = si.groupby(['행정동코드', '자치구', '성별', '연령5'])[['총인구', GI]].sum().reset_index(); T['y'] = T[GI] / T['총인구']
T['y_rel'] = T['y'] / T.groupby(['성별', '연령5'])['y'].transform('mean')
T = T[T['자치구'] == '강남구'].assign(SGIS코드=lambda d: d['행정동코드'].astype(str) + '0'); T = T[T['SGIS코드'] != '11230740']
def dong_mean(col):
    return U.groupby('SGIS코드')[[col, '주민']].apply(lambda g: np.average(g[col], weights=g['주민'].clip(lower=1)))
D = pd.DataFrame({'지수형 (SK 행동)': dong_mean('R'), '학습형 (SK+구조)': dong_mean('SA')})
D = D.join(S[['지역', '읍면동명', '1인세대비율', '65세+비율', '주민인구']]).join(CH[['기초연금 수급률(%)', '장애인 비율(%)', '인구 이동률(%)']])
D = D.join(T.groupby('SGIS코드')['y_rel'].mean().rename('SKT 고립지표 (참고)'))
# 강남 동별 기초생활수급자 (서울시 통계, 2024.12 기준) — '개포3동'은 우리 데이터의 일원2동(11230740)
w = pd.read_csv(EXT / 'seoul_welfare/서울_국민기초생활보장수급자_동별_강남구_2024.csv', header=None, skiprows=4)
w = w[[1, 4]].set_axis(['동', '수급자'], axis=1); w = w[~w['동'].isin(['소계', '기타'])]      # '기타' = 동에 속하지 않는 시설 수급자
w['수급자'] = pd.to_numeric(w['수급자'].replace('-', 0))
codes = pd.read_csv(EXT / 'codes/읍면동_코드대응표_강남_춘천.csv', dtype=str)
w['SGIS코드'] = w['동'].map(dict(zip(codes['읍면동명'].str.replace('(구 일원2동)', '', regex=False), codes['SGIS코드'])))
assert w['SGIS코드'].notna().all() and len(w) == 22
w = w.set_index('SGIS코드').join(S['주민인구'])
D = D.join((w['수급자'] / w['주민인구'] * 100).rename('기초생활수급자 비율(%)'))
print('강남 동별 기초생활수급자 비율(%) 상위 5:'); display(D['기초생활수급자 비율(%)'].dropna().sort_values(ascending=False).head(5).rename(index=dict(zip(D.index, D['읍면동명']))).round(2))
D.round(2)
""")

# ---------------------------------------------------------------- 3
md(r"""
## 3. 독립 검증 — 지역별로 따로
- **수렴**: 고립 위험과 관련된 독립 지표와 같은 방향으로 움직이는가
- **판별**: 인구 규모처럼 관계가 없어야 할 것과는 약해야 함
- ⚠ 학습형은 입력에 주민등록 구조(1인 세대 비율 포함)가 들어 있어, **1인 세대와의 상관은 학습형에 유리하게 부풀려져** 있습니다. 지수형은 주민등록을 입력에 쓰지 않습니다.
""")
code(r"""
CRIT = [('1인세대비율', '수렴', '독립 (행정)'), ('65세+비율', '수렴', '독립 (행정)'), ('기초연금 수급률(%)', '수렴', '독립 (행정)'),
        ('장애인 비율(%)', '수렴', '독립 (행정)'), ('인구 이동률(%)', '수렴', '독립 (행정)'), ('기초생활수급자 비율(%)', '수렴', '독립 (행정)'),
        ('SKT 고립지표 (참고)', '수렴', '같은 출처'), ('주민인구', '판별', '독립 (행정)')]
rows = []
for reg in MAIN:
    d = D[D['지역'] == reg]
    for c, kind, src in CRIT:
        dd = d.dropna(subset=[c])
        if len(dd) < 5: continue
        for mdl in ['지수형 (SK 행동)', '학습형 (SK+구조)']:
            r, p = spearmanr(dd[mdl], dd[c])
            rows.append({'지역': reg, '검증 지표': c, '종류': kind, '출처': src, '모델': mdl, 'ρ': r, 'p': p, 'n': len(dd)})
V = pd.DataFrame(rows)
VP = V.pivot_table(index=['지역', '검증 지표', '종류', '출처'], columns='모델', values=['ρ', 'p']).round(3)
display(VP)
fig, axes = plt.subplots(1, 2, figsize=(13, 3.8), sharex=True)
for ax, reg in zip(axes, MAIN):
    v = V[V['지역'] == reg]; labs = list(dict.fromkeys(v['검증 지표'])); y = np.arange(len(labs))
    for i, (mdl, col) in enumerate([('지수형 (SK 행동)', '#0d366b'), ('학습형 (SK+구조)', '#86b6ef')]):
        vv = v[v['모델'] == mdl].set_index('검증 지표').reindex(labs)
        ax.barh(y + (i - .5) * .38, vv['ρ'], height=.36, color=col, label=mdl)
        for yy, (rr, pp) in enumerate(zip(vv['ρ'], vv['p'])):
            if pp < .05: ax.text(rr + (.02 if rr > 0 else -.02), yy + (i - .5) * .38, '*', va='center', ha='left' if rr > 0 else 'right', color=INK)
    ax.set_yticks(y, labs, fontsize=8.5); ax.axvline(0, color=INK2, lw=.8); ax.set_xlim(-1, 1); ax.set_title(f'{reg} — 동 단위 순위 상관 ρ (* p<0.05)')
axes[0].legend(fontsize=8.5, loc='lower right'); plt.tight_layout(); plt.show()
""")

# ---------------------------------------------------------------- 4
md("## 4. 강남 SKT 정답과의 비교 (참고) — 정답을 쓰지 않은 지수형이 얼마나 맞히나")
code(r"""
E = U[U['지역'] == '서울 강남구'].merge(T[['SGIS코드', '성별', '연령5', 'y_rel']], on=['SGIS코드', '성별', '연령5'])
res = {}
for nm, col in [('지수형 (정답 미사용)', 'R'), ('학습형 (전체로 학습, 참고)', 'SA')]:
    wr = np.array([spearmanr(g['y_rel'], g[col])[0] for _, g in E.groupby('cell')])
    bs = [np.random.default_rng(i).choice(wr, len(wr)).mean() for i in range(1000)]
    res[nm] = {'동 순위 ρ (같은 성·연령 안)': wr.mean(), '95% 구간': f'{np.percentile(bs, 2.5):.2f}~{np.percentile(bs, 97.5):.2f}'}
res['학습형 (동 하나씩 빼고 검증, 04 결과)'] = {'동 순위 ρ (같은 성·연령 안)': 0.485, '95% 구간': '0.33~0.63'}
pd.DataFrame(res).T
""")

# ---------------------------------------------------------------- 5
md("## 5. 두 모델의 우선순위는 얼마나 겹치나")
code(r"""
rows = []
for reg in MAIN:
    u = U[U['지역'] == reg]; n = int(len(u) * .15)
    a, b = set(u.nlargest(n, 'R').index), set(u.nlargest(n, 'SA').index)
    a2, b2 = set(u.nlargest(n, '지수형 점수').index), set(u.nlargest(n, '위험점수').index)
    rows.append({'지역': reg, '행동 점수 순위 상관': spearmanr(u['R'], u['SA'])[0], '행동 점수 상위 15% 겹침(%)': len(a & b) / n * 100,
                 '최종 점수 순위 상관': spearmanr(u['지수형 점수'], u['위험점수'])[0], '최종 점수 상위 15% 겹침(%)': len(a2 & b2) / n * 100})
display(pd.DataFrame(rows).set_index('지역').round(2))
for reg in MAIN:
    u = U[U['지역'] == reg]
    only_idx = u.nlargest(int(len(u) * .15), '지수형 점수').index.difference(u.nlargest(int(len(u) * .15), '위험점수').index)
    print(f'[{reg}] 지수형에서만 상위 15%에 드는 집단:'); display(u.loc[only_idx, ['읍면동명', '성별', '연령5', '주민', '지수형 점수', '위험점수']].round(1).head(8))
""")
md("## 6. 요약 · 기존(04 학습형)과의 차이")
SUM = Path(__file__).with_name('s4b.md')
md(SUM.read_text(encoding='utf-8') if SUM.exists() else '_(실행 결과를 바탕으로 작성)_')

if hasattr(flow, 'FLOW4B'):
    flow.annotate(cells, flow.FLOW4B, flow.TITLE4B)
nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}})
nbf.write(nb, OUT); print('written', OUT, len(cells))
