"""Generate explicitly synthetic usage history from pinned board dimensions.
Offline only; same Shapely environment as build_usage_basis. No random sampling.
"""
import hashlib
import json
import math
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
from .build_usage_basis import ROOT, parse, child, children

OUT=Path(__file__).with_name('data')/'process_usage_history_v1.json'
WIRE='process-material:kester:275:2470687601'
IPA='process-material:mgchemicals:824'
BAR='process-material:kester:sac305:bar'
ROUTE='assumed:wave-main-hand-bat-local-clean:v1'

def text(x):return str(Decimal(str(x)).quantize(Decimal('.000001'),rounding=ROUND_CEILING))

def build():
    refpath=ROOT/'data/production_planning/process_materials_v1.json'
    ref=json.loads(refpath.read_text());records=[];anchors=[]
    conditions=[(100,'2026-09-05',2.8,2.5,.10),(150,'2026-09-15',3.0,2.8,.12),(200,'2026-09-25',3.1,3.0,.15),(250,'2026-10-05',3.2,2.7,.13),(300,'2026-10-15',2.9,2.6,.11),(400,'2026-10-25',3.0,2.9,.14)]
    catalog=json.loads((ROOT/ref['existing_catalog']['path']).read_text())
    for c in ref['configuration_links']:
        cid=c['configuration_id'];rev=cid.split(':')[-2]
        src=next(s for s in ref['sources'] if s['id']=='source:process-materials:v1:'+rev)
        raw=(ROOT/src['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==src['sha256']
        board=parse(raw.decode());modules={next(x[2] for x in children(m,'fp_text') if x[1]=='reference'):m for m in children(board,'module')}
        populated={p for n in catalog['nodes'] if n['type']=='BOMItem' and n['configuration_id']==cid and n['population']=='populated' for p in n['positions']}
        assert {'BAT1','C25','C27','LAN_CON1'}<=populated
        def pins(ref,ids):
            rows=[]
            for p in children(modules[ref],'pad'):
                if p[1] not in ids or p[2]!='thru_hole':continue
                drill=child(p,'drill');assert len(drill)==2
                rows.append(dict(reference=ref,pin=p[1],drill_mm=drill[1]))
            assert len(rows)==len(ids)
            return rows
        hand=pins('BAT1',{'1','2'})
        wave=pins('C25',{'1','2'})+pins('C27',{'1','2'})+pins('LAN_CON1',{str(i) for i in range(1,11)})
        thickness=float(child(child(board,'general'),'thickness')[1]);area=float(c['outline']['area_cm2'])
        # Core density 1 g/cm³ and circular 0.6mm leads are assumptions, not manufacturer facts.
        wire_density=1/(.978/7.37+.022/1)
        lead_diameter=.6;fillet_multiplier=1.3
        joint_volume=sum(math.pi/4*(float(p['drill_mm'])**2-lead_diameter**2)*thickness for p in wave)*fillet_multiplier
        anchors.append(dict(configuration_id=cid,source_id=src['id'],source_path=src['path'],source_sha256=src['sha256'],
            hand_pins=hand,wave_pins=wave,board_thickness_mm=str(thickness),one_side_area_cm2=c['outline']['area_cm2'],
            assumptions=dict(wire_core_density_g_cm3='1',wave_lead_diameter_mm='0.6',fillet_volume_multiplier='1.3',
                ipa_wet_film_mm='0.1',ipa_sides=2,ipa_wipe_passes=2,wire_feed_loss_fraction='0.05',
                already_charged_solder_bath=True,scope='가상 작업 범위: BAT1 수작업, C25/C27/LAN_CON1 전기핀 납조. 실제 제조 공정·핀 규격·세정 필요 여부 미확인. 기타 접점과 초기 납조 충전은 이 이력의 산정 범위 밖.')))
        for index,(q,day,feed,retention,dross) in enumerate(conditions,1):
            wire=math.pi*(.8/2)**2*wire_density/1000*len(hand)*feed*1.05
            cleaner=area*2*(.1/10)*2*retention
            bar=joint_volume*7.37/1000*(1+dross)
            entries=[(WIRE,'g',wire,'0.5','10',dict(feed_mm_per_joint=str(feed),hand_joint_count=len(hand),estimated_wire_density_g_cm3=text(wire_density)),
                      'π×(0.8/2)²×가정 코어 포함 밀도÷1000×BAT1 2접점×접점당 투입길이×1.05'),
                     (IPA,'mL',cleaner,'10','20',dict(wipe_retention_evaporation_factor=str(retention)),
                      '실제 한 면 면적×2면×가정 젖음두께 0.01cm×2회×천 잔류/증발 계수'),
                     (BAR,'g',bar,'2','10',dict(dross_fraction=str(dross),joint_volume_mm3=text(joint_volume)),
                      '실제 드릴과 보드 두께/가정 핀으로 산정한 접합체적×7.37÷1000×(1+드로스율)')]
            for mid,unit,per,setup,returned,drivers,formula in entries:
                variable=Decimal(text(per*q));net=variable+Decimal(setup)
                records.append(dict(id=f'synthetic-usage:{rev}:{index}:{mid.split(":")[-1]}',
                    evidence_id=f'evidence:synthetic-usage:v1:{rev}:{index}:{mid.split(":")[-1]}',
                    origin='synthetic',status='completed',configuration_id=cid,material_id=mid,unit=unit,route_id=ROUTE,
                    batch_id=f'SYN-{rev}-{day}',completed_at=day+'T08:00:00+00:00',produced_quantity=q,
                    issued=str(net+Decimal(returned)),returned=returned,setup_used=setup,
                    simulated_variable_used=str(variable),drivers=drivers,generation_formula=formula,
                    basis='물리모델로 생성한 가상 기록이며 실제 생산/출고/반납/실측 기록이 아님. 모델에서 생성한 이력으로 모델 정확도를 검증할 수 없음.'))
    return dict(format='synthetic-process-usage-history/1.0',origin='synthetic',version=1,as_of='2026-11-02T00:00:00+00:00',
        reference_sha256=hashlib.sha256(refpath.read_bytes()).hexdigest(),route_id=ROUTE,
        source_commit=ref['existing_catalog']['source_commit'],geometry_anchors=anchors,records=records,
        policy=dict(random_used=False,minimum_batches=3,maximum_recent_batches=6,statistical_confidence_claimed=False,
                    actual_inventory_modified=False,allowance_is_additional_buffer=True),
        generator='ontologics.production_planning.build_usage_history')
if __name__=='__main__':
    OUT.write_text(json.dumps(build(),ensure_ascii=False,indent=2)+'\n');print(OUT)
