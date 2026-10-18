"""실제 응답 CSV를 결합해 기술통계를 낸다. 빈 응답·중복·배정 불일치는 실패한다.

python analyze.py 응답1.csv 응답2.csv ... --out 별도결과폴더
개별 내용의 사실성·누락 채점은 채점기준.md에 따라 평가자가 따로 수행한다.
"""
from pathlib import Path
import argparse, csv, json, statistics

def summarize(paths):
    rows=[]
    for p in paths:
        with open(p,encoding='utf-8-sig',newline='') as f: rows.extend(csv.DictReader(f))
    if not rows: raise ValueError('실제 응답이 0건입니다. 효과를 계산하지 않습니다.')
    with open(Path(__file__).with_name('배정표.csv'),encoding='utf-8-sig',newline='') as f:
        assignment={(r['rater_id'],r['case_id']):r['condition'] for r in csv.DictReader(f)}
    seen=set();times={k:[] for k in 'ABC'}; raters={k:set() for k in 'ABC'}
    for r in rows:
        key=(r['rater_id'].strip(),r['case_id'].strip());c=r['condition'].strip()
        if key in seen: raise ValueError(f'중복 응답: {key}')
        if assignment.get(key)!=c: raise ValueError(f'배정표와 다른 응답: {key}, {c}')
        t=float(r['elapsed_seconds']); difficulty=int(r['difficulty_1to5'])
        if not 0<t<86400 or not 1<=difficulty<=5: raise ValueError(f'시간 또는 난이도 범위 오류: {key}')
        for k in ['selected_places','reason','counter_or_missing','resource','eligibility_checks','next_action']:
            if not r[k].strip(): raise ValueError(f'미응답 항목: {key}, {k}')
        seen.add(key); times[c].append(t);raters[c].add(key[0])
    result={'response_count':len(rows),'expected_count':len(assignment),'complete':len(rows)==len(assignment),'conditions':{}}
    for c,ts in times.items():
        result['conditions'][c]={'n_responses':len(ts),'n_raters':len(raters[c]),'median_seconds':statistics.median(ts) if ts else None}
    result['interpretation']='반복 측정이므로 응답 행을 독립된 참여자 수로 세지 않습니다. 시간만으로 개선을 결론내리지 않고 별도 블라인드 사실·누락 채점과 함께 해석합니다. 미완료 배정은 최종 비교로 사용하지 않습니다.'
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('files',nargs='+',type=Path);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
    try: r=summarize(a.files)
    except (ValueError,KeyError) as e: p.error(str(e))
    a.out.mkdir(parents=True,exist_ok=True);(a.out/'기술통계.json').write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(r,ensure_ascii=False))
