from pathlib import Path
import nbformat as nbf
import sys as _sys; _sys.path.insert(0, str(Path(__file__).parent)); import flow

OUT = Path(__file__).resolve().parent.parent / '06_AI에이전트_설계.ipynb'
cells = []
md = lambda s: cells.append(nbf.v4.new_markdown_cell(s.strip()))
code = lambda s: cells.append(nbf.v4.new_code_cell(s.strip()))

md(r"""
# 06
**에이전트의 역할 (범위를 좁게 정함)**: 데이터로 개인의 고립을 판정하지 않습니다. 대신 **"고독사 위험자 판단도구로 현장 조사를 나갈 동·집단의 우선순위"**를 근거와 연계 서비스와 함께 추천합니다. 판단도구 13점 중 데이터로 대리할 수 있는 문항은 2점뿐이기 때문입니다(03, 해석주의사항 15).

| 단계 (주제 설명서의 운영 흐름) | 하는 일 | 근거 데이터 · 산출물 |
|---|---|---|
| ① 탐지 | 매월: 동 × 성 × 연령의 행동 고립 H · 집단 소비 C 갱신 / 매일: 카드 활동 급감(T), 기상특보 | SK · 신한카드 · 기상청 (04, 부록 A 9절) |
| ② 판단 | 5단계와 위험 유형(이중 위험·행동형·취약형) | 04 결과 |
| ③ 원인 분석 | 그 집단이 위험한 이유를 지표로 설명 | 04 '주요 요인' |
| ④ 추천 | 유형 × 생애단계에 맞는 복지서비스, 지역 공백 알림 | 05 결과 |
| ⑤ 우선 대상 | 지역 · 생애단계 · 성별 목록 → 현장 조사 → 결과를 돌려받아 기준 보정 | 이 노트북 |
""")
code(r"""
import warnings; warnings.filterwarnings('ignore')
from pathlib import Path
import numpy as np, pandas as pd
from scipy.stats import norm
pd.set_option('display.max_columns', 30); pd.set_option('display.width', 220); pd.set_option('display.max_colwidth', 70)
EXT, OUTD = Path('external'), Path('outputs')
MAIN = ['서울 강남구', '강원 춘천시']; ORDER = ['심각', '경계', '주의', '관심', '정상']
R = pd.read_csv(OUTD / '위험도_2축_강남_춘천_동x성x연령.csv', dtype={'SGIS코드': str})
REC = pd.read_csv(OUTD / '복지자원_추천_유형x생애단계.csv')
T_PERF = pd.read_csv(OUTD / '시점신호_탐지성능.csv')
W = pd.read_csv(EXT / 'weather/kma_asos_daily_서울_춘천_202507_202512.csv', parse_dates=['일자'])
print('위험도 집단:', len(R), '| 추천표:', len(REC), '| 날씨 일수:', W.groupby('지역').size().to_dict())
""")

md("## 1. 에이전트 도구 — 각 단계를 함수 하나로")
md(r"""
에이전트는 아래 함수를 차례로 부릅니다. 모든 판단은 **규칙과 04·05의 숫자**로 정해지고, 함수마다 근거를 함께 돌려줍니다. LLM은 쓰더라도 마지막에 이 결과를 읽기 쉬운 문장으로 바꾸는 데만 씁니다(4절).
""")
code(r"""
def judge(region):                                     # ② 판단: 집단별 단계·유형 (04 결과)
    return R[R['지역'] == region].copy()
def explain(row):                                      # ③ 원인 분석: 주요 요인 → 문장
    f = str(row['주요 요인']).replace(' (+ 집단 소비 위축)', '')
    s = f"{row['읍면동명']} {row['성별']} {row['연령5']} 집단({int(row['주민']):,}명)은 '{row['위험 유형']}', 지역 내 '{row['단계']}' 단계입니다."
    if f != '—': s += f" 같은 성·연령의 다른 동과 비교한 특징: {f}."
    if '집단 소비 위축' in str(row['주요 요인']): s += ' 이 성·연령 집단의 외부활동 소비도 지역 평균보다 위축돼 있습니다.'
    return s
def recommend(row, k=3):                               # ④ 추천: 05의 유형 × 생애단계 표
    r = REC[(REC['지역'] == row['지역']) & (REC['연령5'] == row['연령5']) & (REC['위험 유형'] == row['위험 유형'])].nsmallest(k, '순위')
    return list(r['서비스'])
def priority(region, by='지역', top=8):                 # ⑤ 우선 대상: 세 가지 목록으로 청년·여성 누락을 막음
    d = judge(region)
    if by == '지역':
        return d[d['단계'] == '심각'].nlargest(top, '우선순위 점수')
    if by == '생애단계':
        return d.sort_values('우선순위 점수', ascending=False).groupby('연령5').head(1)
    if by == '성별':
        return d[d['성별 내 단계'] == '심각'].sort_values('우선순위 점수', ascending=False).groupby('성별').head(top // 2)
def raise_level(level, steps=1):                       # 실시간 신호가 오면 단계를 한 칸 올림 (부록 A 9절 규칙)
    return ORDER[max(0, ORDER.index(level) - steps)]
row = priority('강원 춘천시').iloc[0]
print(explain(row)); print('추천:', recommend(row))
""")

md("## 2. 실시간 트리거 — 카드 활동 급감(T)과 기상특보")
md(r"""
**T 규칙 (부록 A 9절, 동결)**: 날씨·요일·명절을 보정한 카드 활동이 7일 평균 −10% 이하로 7일 이어지면 그 지역 × 성 × 연령의 단계를 한 칸 올립니다. 실제 2025년 하반기 데이터에서는 한 번도 울리지 않았고(오경보 0건), 감소를 인위로 넣은 시험에서는 아래처럼 잡혔습니다.

**기상 트리거 (설계)**: 기상청 한파·폭염 특보가 나오면, 날씨 영향을 많이 받는 집단(행동형·이중 위험 60대+)에 **사전 안부 확인**을 추천합니다. 특보 API는 아직 신청 전이라, 여기서는 기상청 ASOS 관측값으로 **특보 기준에 해당했을 날**을 대신 셉니다.
- 한파 기준: 아침 최저기온 −12℃ 이하가 2일 이상
- 폭염 기준: 일 최고기온 33℃ 이상이 2일 이상 (공식 기준은 체감온도라 근사)
""")
code(r"""
print('T 규칙 탐지 성능 (부록 A, 감소를 인위로 넣은 시험)'); display(T_PERF.round(1))
def runs(flag, n=2):                                   # n일 이상 이어진 날만 남김
    g = (flag != flag.shift()).cumsum(); size = flag.groupby(g).transform('size')
    return flag & (size >= n)
W = W.sort_values(['지역', '일자'])
W['한파'] = W.groupby('지역')['최저기온'].transform(lambda s: runs(s <= -12))
W['폭염'] = W.groupby('지역')['최고기온'].transform(lambda s: runs(s >= 33))
TRIG = W[W['한파'] | W['폭염']][['지역', '일자', '최저기온', '최고기온', '한파', '폭염']]
print('기상 트리거가 울렸을 날 수:'); display(W.groupby('지역')[['한파', '폭염']].sum())
def weather_targets(region):                           # 특보 시 사전 안부 확인 대상
    d = judge(region)
    return d[(d['연령5'] == '60대+') & d['위험 유형'].isin(['이중 위험', '행동형'])].nlargest(5, '우선순위 점수')
for reg in MAIN:
    print(f'[{reg}] 특보 시 사전 안부 확인 대상 (60대+ 행동형·이중 위험 상위 5)')
    display(weather_targets(reg)[['읍면동명', '성별', '연령5', '주민', '위험 유형', '단계', '주요 요인']])
""")

md("## 3. 브리핑 생성 — 담당자가 받는 결과물")
code(r"""
def table(df, cols):
    head = '| ' + ' | '.join(cols) + ' |\n|' + '---|' * len(cols) + '\n'
    return head + ''.join('| ' + ' | '.join(str(v) for v in r) + ' |\n' for r in df[cols].values)
def brief(region):
    d = judge(region); L = [f'# {region} 사회적 고립 위험 브리핑', '',
         '> 기준 데이터: SK 유동인구·신한카드 2025.7~12, 주민등록 2024.12·2025.12. **집단 단위 추정이며 개인 판정이 아닙니다.** 최종 판단은 고독사 위험자 판단도구 현장 조사로 합니다.', '']
    top = priority(region).copy(); top['추천 서비스'] = top.apply(lambda r: ' / '.join(recommend(r)), axis=1)
    top['이유'] = top['주요 요인'].str.replace(' (+ 집단 소비 위축)', ' · 소비 위축', regex=False)
    top['주민'] = top['주민'].map(lambda v: f'{v:,}' + (' *' if v < 300 else ''))
    L += ['## 1. 이번 달 우선 현장 조사 대상 (지역 내 \'심각\')', '', table(top, ['읍면동명', '성별', '연령5', '주민', '위험 유형', '이유', '추천 서비스']),
          '\\* 주민 300명 미만 집단: 비율 지표가 불안정할 수 있어 동 담당자의 현황 확인을 먼저 권합니다.', '']
    ls = priority(region, '생애단계').sort_values('연령5'); ls['주민'] = ls['주민'].map('{:,}'.format)
    L += ['## 2. 생애단계별 1순위 (청년·중년 누락 방지)', '', table(ls, ['연령5', '읍면동명', '성별', '주민', '위험 유형', '단계', '생애단계 내 단계'])]
    sx = priority(region, '성별'); sx['주민'] = sx['주민'].map('{:,}'.format)
    L += ['## 3. 성별 안 \'심각\' 상위 (여성 누락 방지)', '', table(sx, ['성별', '읍면동명', '연령5', '주민', '위험 유형', '단계'])]
    dg = d[d['단계'].isin(['심각', '경계'])].groupby('읍면동명').agg(위험_인구=('주민', 'sum'), 집단=('주민', 'size'),
         주된_유형=('위험 유형', lambda s: s.value_counts().index[0])).sort_values('위험_인구', ascending=False).head(5).reset_index()
    dg['위험_인구'] = dg['위험_인구'].map('{:,}'.format)
    L += ['## 4. 심각·경계 인구가 많은 동 상위 5', '', table(dg, ['읍면동명', '위험_인구', '집단', '주된_유형'])]
    L += ['## 5. 자원 공백 알림', '', f'- {region}의 시군구 자체 관계·정서(관계 회복) 서비스는 **0개**입니다(05). 행동형 집단은 중앙·광역 서비스(안부살핌 우편, 마음 상담 바우처 등)로 연결하고, 지역 프로그램 신설을 검토해 주세요.', '']
    t = W[(W['지역'] == region)]
    L += ['## 6. 실시간 신호', '', f"- 카드 활동 급감(T): 분석 기간 중 발생 0건 (규칙: 7일 평균 −10% 이하 7일 지속 → 단계 한 칸 상향)",
          f"- 기상 트리거 해당일: 한파 {int(t['한파'].sum())}일, 폭염 {int(t['폭염'].sum())}일 → 해당일에는 아래 60대+ 행동형·이중 위험 집단에 사전 안부 확인", '',
          table(weather_targets(region).assign(주민=lambda x: x['주민'].map('{:,}'.format)), ['읍면동명', '성별', '연령5', '주민', '위험 유형', '단계'])]
    return '\n'.join(L)
for reg in MAIN:
    txt = brief(reg); fn = OUTD / f"에이전트_브리핑_{reg.split()[1]}.md"; fn.write_text(txt, encoding='utf-8')
    print('저장:', fn)
from IPython.display import Markdown; display(Markdown(brief('서울 강남구')))
""")

md("## 4. LLM을 쓰는 부분과 쓰지 않는 부분 (선택 사항)")
md(r"""
| 부분 | LLM 사용 | 이유 |
|---|---|---|
| 점수 · 단계 · 유형 · 순위 | **쓰지 않음** | 재현 가능해야 하고 숫자 근거가 있어야 함 (규칙·04 결과) |
| 서비스 후보 고르기 | 쓰지 않음 (규칙) | 05의 규칙 표 — 담당자가 검토 가능 |
| 브리핑 문장 다듬기, 담당자 질문에 답하기 | 쓸 수 있음 | 위 함수 결과만 입력으로 넣고, 숫자는 바꾸지 못하게 함 |

**가드레일 (프롬프트에 넣을 규칙)**
1. 입력 표에 없는 숫자·동·서비스를 만들지 말 것
2. 개인을 지목하거나 '고립된 사람'이라고 단정하지 말 것 — '현장 조사가 필요한 집단'으로 표현
3. 모든 문장에 근거 지표(주요 요인)를 붙일 것
4. 판단도구 조사로 확인하라는 문구를 항상 포함할 것
""")
code(r"""
PROMPT = '''당신은 구청 복지 담당자를 돕는 보조원입니다. 아래 [데이터]에 있는 사실만 써서 3~5문장으로 요약하세요.
규칙: (1) 데이터에 없는 숫자·장소·서비스를 만들지 않는다 (2) 개인을 지목하지 않고 '현장 조사가 필요한 집단'으로 쓴다
(3) 각 집단마다 이유(주요 요인)를 함께 쓴다 (4) 마지막 문장은 '고독사 위험자 판단도구로 현장에서 확인이 필요합니다.'로 끝낸다.
[데이터]
{facts}'''
facts = '\n'.join(explain(r) + ' 추천: ' + ', '.join(recommend(r)) for _, r in priority('강원 춘천시', top=3).iterrows())
print(PROMPT.format(facts=facts))
""")

md("## 5. 현장 파일럿과 피드백 고리 — 실제 고립과 연결하는 방법")
md(r"""
지금까지의 검증은 모두 '고립과 관련된 다른 지표'와의 상관입니다. **실제 고립과의 연결**은 현장 조사로만 확인할 수 있어, 에이전트에 파일럿과 피드백을 넣습니다.

1. **표본**: 각 지역에서 '심각' 집단이 많은 동과 '정상' 위주 동을 짝지어, 같은 성·연령의 주민을 판단도구로 조사합니다(복지 담당자 방문·전화).
2. **판정**: 위험군(6점 이상) 비율이 우선 대상 쪽에서 더 높은지 비교합니다(적중률 향상도).
3. **피드백**: 조사 결과를 정답으로 삼아 H·V 구성 지표의 가중치를 다시 정합니다. 이번에는 SKT가 아니라 **실제 고립 판정**이 정답이므로 순환 문제가 없습니다.
4. **운영 지표 (KPI)**: 적중률 향상도, 서비스 연계율, 3개월 뒤 재조사 시 점수 개선율, 실시간 경보의 현장 확인 비율

아래는 필요한 조사 인원입니다. 기준 위험군 비율과 기대 차이는 **가정값**이므로 파일럿 전에 지자체 자료로 바꿔야 합니다.
""")
code(r"""
def n_per_group(p0, p1, alpha=.05, power=.8):           # 두 비율 차이 검정에 필요한 집단당 인원
    pb = (p0 + p1) / 2; za, zb = norm.ppf(1 - alpha / 2), norm.ppf(power)
    return int(np.ceil((za * np.sqrt(2 * pb * (1 - pb)) + zb * np.sqrt(p0 * (1 - p0) + p1 * (1 - p1))) ** 2 / (p1 - p0) ** 2))
rows = [{'비교 집단 위험군 비율 (가정)': f'{p0:.0%}', '우선 대상 위험군 비율 (가정)': f'{p1:.0%}', '향상도': p1 / p0, '집단당 필요 인원': n_per_group(p0, p1)}
        for p0, p1 in [(.10, .20), (.10, .15), (.15, .25), (.20, .30)]]
pd.DataFrame(rows).round(2)
""")
md("## 6. 요약")
SUM = Path(__file__).with_name('s6.md')
md(SUM.read_text(encoding='utf-8') if SUM.exists() else '_(실행 결과를 바탕으로 작성)_')

if hasattr(flow, 'FLOW6'):
    flow.annotate(cells, flow.FLOW6, flow.TITLE6)
nb = nbf.v4.new_notebook(cells=cells, metadata={"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}, "language_info": {"name": "python"}})
nbf.write(nb, OUT); print('written', OUT, len(cells))
