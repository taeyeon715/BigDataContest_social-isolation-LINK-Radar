from pathlib import Path
from datetime import date
import sys,json
import pandas as pd
from bigcontest.config import ROOT
R=ROOT;O=R/'external/resources/chuncheon_centers'
from bigcontest.agent.tools.datasource import DataSource,dev_files,files_snapshot_id,register_source
from bigcontest.rules.compare import ctx_for,collect,run_rules
from bigcontest.resources.match import access_gap,find_resources
out=R/'reports/a1_접근성검증';out.mkdir(parents=True,exist_ok=True)
names=pd.read_csv(R/'ref/adm_cd_names.csv',dtype=str).set_index('adm_nm')
target=['남면','남산면','북산면'];res=[];summ=[];candidates=[]
for label,path in [('보강 전',O/'보강전_원장.csv'),('경로당 보강',R/'external/resources/복지자원_원장_v2.csv')]:
    led=pd.read_csv(path,dtype=str,keep_default_na=False)
    files=dev_files(R)|{'resources':path};sid=files_snapshot_id(files);ds=DataSource(sid,root=R,files=files);register_source(sid,ds)
    for nm in target:
        n=names.loc[nm];origin=(float(n.lat),float(n.lon));adm=n.adm_cd
        for grp in ['고령층','중장년 남성 1인가구','청년 남성']:
            found=find_resources(led,adm,grp,'안부·관계',origin=origin)
            spatial=sorted([m for m in found if m.distance_km is not None],key=lambda m:m.distance_km)
            nearest=spatial[0]
            res.append({'판':label,'동':nm,'집단':grp,'가장 가까운 자원':nearest.name,'거리(km)':round(nearest.distance_km,4),
                        '자원 ID':nearest.resource_id,'좌표 있는 자원 수':len(spatial),'비고':'현재 원장 기준·직선거리·실제 운영 미확인'})
    for dt in [date(2025,9,26),date(2025,10,13),date(2025,12,20)]:
        ctx=ctx_for(sid,'춘천',dt,'고령층');ev=collect(ctx);cs=run_rules(ev,ctx.request,dt,10).candidates
        sched=[c for c in cs if c.candidate_type=='scheduled_access_review']
        summ.append({'판':label,'기준일':str(dt),'집단':'고령층','점검 후보':len(sched),'Level 3':sum(c.candidate_level=='Level 3' for c in sched),
                     'Level 2':sum(c.candidate_level=='Level 2' for c in sched)})
        for c in sched:
            if c.adm_cd in set(names.loc[target].adm_cd):
                candidates.append({'판':label,'기준일':str(dt),'동':names[names.adm_cd==c.adm_cd].index[0],'등급':c.candidate_level,'사유':c.level_reason})
pd.DataFrame(res).to_csv(out/'세면_거리비교.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(summ).to_csv(out/'고령층_등급비교.csv',index=False,encoding='utf-8-sig')
pd.DataFrame(candidates).to_csv(out/'세면_등급비교.csv',index=False,encoding='utf-8-sig')
print(json.dumps({'distance':res,'summary':summ,'candidates':candidates},ensure_ascii=False,indent=2))
