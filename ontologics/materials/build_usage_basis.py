"""Offline extraction for the pinned KiCad 5 boards; requires Shapely 2.

Run from repository root: python -m ontologics.materials.build_usage_basis
No Shapely dependency is introduced to the web application. Unsupported geometry
fails closed. Output is a conditional estimate, not an approved stencil export.
"""
import hashlib
import json
import re
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from shapely.affinity import rotate, translate
from shapely.geometry import LineString, box
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).with_name('data') / 'per_board_usage_v1.json'


def parse(text):
    stack = [[]]
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text):
        if token == '(':
            stack.append([])
        elif token == ')':
            node = stack.pop()
            stack[-1].append(node)
        else:
            stack[-1].append(token[1:-1] if token.startswith('"') else token)
    if len(stack) != 1 or len(stack[0]) != 1:
        raise ValueError('Invalid KiCad s-expression')
    return stack[0][0]


def children(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def child(node, key, default=None):
    return next(iter(children(node, key)), default)


def geometry(board, population):
    groups = {s: {'populated': [], 'unknown': [], 'absent': []} for s in ('F.Paste', 'B.Paste')}
    records, lines, traps = [], 0, 0
    setup = child(board, 'setup')
    if child(setup, 'pad_to_paste_clearance') or child(setup, 'pad_to_paste_clearance_ratio'):
        raise ValueError('Global paste override requires review')
    for module in children(board, 'module'):
        ref = next(x[2] for x in children(module, 'fp_text') if x[1] == 'reference')
        state = population.get(ref, 'absent')
        if state not in ('populated', 'unknown'):
            state = 'absent'
        at = child(module, 'at')
        mx, my = map(float, at[1:3])
        angle = float(at[3]) if len(at) > 3 else 0
        shapes = {'F.Paste': [], 'B.Paste': []}
        for p in children(module, 'pad'):
            layers = [x for x in child(p, 'layers')[1:] if x in shapes]
            if not layers:
                continue
            if p[3] not in ('rect', 'trapezoid'):
                raise ValueError('Unsupported paste pad shape: ' + p[3])
            if p[3] == 'trapezoid':
                if any(float(x) != 0 for x in child(p, 'rect_delta', ['rect_delta', '0', '0'])[1:]):
                    raise ValueError('Nonzero trapezoid delta requires polygon implementation')
                traps += 1  # KiCad zero-delta trapezoid is a rectangle.
            w, h = map(float, child(p, 'size')[1:])
            margin = float(child(p, 'solder_paste_margin', child(module, 'solder_paste_margin', ['', '0']))[1])
            ratio = float(child(p, 'solder_paste_margin_ratio', child(module, 'solder_paste_ratio', ['', '0']))[1])
            if ratio:
                raise ValueError('Ratio override requires review')
            w, h = w + 2 * margin, h + 2 * margin
            if min(w, h) <= 0:
                raise ValueError('Invalid paste opening')
            pa = child(p, 'at')
            # KiCad 5 pad angle is absolute; pad centre is footprint-local.
            pa_angle = float(pa[3]) if len(pa) > 3 else 0
            shape = rotate(box(-w/2, -h/2, w/2, h/2), -(pa_angle-angle), origin=(0, 0))
            shape = translate(shape, float(pa[1]), float(pa[2]))
            for layer in layers:
                shapes[layer].append(shape)
        for item in module:
            if not isinstance(item, list):
                continue
            layer = child(item, 'layer')
            if not layer or layer[1] not in shapes:
                continue
            if item[0] != 'fp_line':
                raise ValueError('Unsupported paste graphic: ' + item[0])
            lines += 1
            shape = LineString([list(map(float, child(item, k)[1:])) for k in ('start', 'end')])
            shape = shape.buffer(float(child(item, 'width')[1])/2, quad_segs=64)
            shapes[layer[1]].append(shape)
        for layer, parts in shapes.items():
            if not parts:
                continue
            merged = unary_union(parts)
            records.append(dict(reference=ref, population=state, layer=layer,
                                area_mm2=round(merged.area, 9), primitive_count=len(parts)))
            placed = translate(rotate(merged, -angle, origin=(0, 0)), mx, my)
            groups[layer][state].append(placed)
    # Root-level paste graphics are not silently discarded.
    for item in board:
        if isinstance(item, list) and child(item, 'layer') and child(item, 'layer')[1] in groups:
            raise ValueError('Root paste graphic requires review')
    by_side = {s: {k: unary_union(v).area for k, v in g.items()} for s, g in groups.items()}
    return dict(populated_area_mm2=round(sum(g['populated'] for g in by_side.values()), 9),
                unknown_population_area_mm2=round(sum(g['unknown'] for g in by_side.values()), 9),
                excluded_not_in_bom_area_mm2=round(sum(g['absent'] for g in by_side.values()), 9),
                by_side=by_side, by_reference=records, paste_line_count=lines, zero_delta_trapezoids=traps,
                method='Union per population and side, round-ended line strokes (64 segments/quadrant); pad margins applied. Unknown population excluded from base. PCB paste geometry is not a released stencil.')


def rounded(value):
    return str(Decimal(str(value)).quantize(Decimal('0.000001'), rounding=ROUND_CEILING))


def build():
    path = ROOT / 'data/production_planning/process_materials_v1.json'
    ref = json.loads(path.read_text())
    catalog = json.loads((ROOT / ref['existing_catalog']['path']).read_text())
    for source in ref['sources'] + [ref['existing_catalog']]:
        if hashlib.sha256((ROOT / source['path']).read_bytes()).hexdigest() != source['sha256']:
            raise ValueError('Source hash changed')
    configs = []
    for c in ref['configuration_links']:
        cid = c['configuration_id']; rev = cid.split(':')[-2]
        source = next(s for s in ref['sources'] if s['id'] == 'source:process-materials:v1:' + rev)
        board = parse((ROOT / source['path']).read_text())
        population = {p: n['population'] for n in catalog['nodes'] if n['type'] == 'BOMItem' and n['configuration_id'] == cid for p in n['positions']}
        geo = geometry(board, population)
        coefficient = ref['materials'][0]['parameters']['derived_reference']['mass_g_per_mm2_aperture']
        paste = rounded(Decimal(str(geo['populated_area_mm2'])) * Decimal(coefficient))
        flux = str(Decimal(c['flux_reference_scenario']['wet_flux_deposited_g_per_board'][1]).quantize(
            Decimal('0.0001'), rounding=ROUND_CEILING))
        material_rows = []
        descriptions = [
            ('도면 기반 조건부 추정', paste, 'g', 'PCB Paste 도형 합집합 × 0.1016 mm × 4.2227939196 g/cm³ ÷ 1000',
             'BOM populated 위치만 포함. 앞·뒷면 합산. 두께 0.1016 mm는 Indium 공개 예제값이며 실제 스텐실 사양 미확인. 인쇄 손실·초기 비드 별도.',
             ['실제 스텐실 두께/개구부와 WROOM 적용 확인', '초기 비드와 회수되지 않는 잔량']),
            ('투입 길이 기준 필요', None, 'g', '와이어 실제 계량 g/m × 대당 투입 길이 m',
             '제조사 외경 0.8 mm, 코어 플럭스 2.2%. 외경과 합금 밀도만으로 코어 포함 선밀도를 확정하지 않음. SMT 열패드의 thru_hole을 수삽 접점으로 세지 않음.',
             ['수작업 접합 대상과 접점별 와이어 투입 길이/질량', '재작업 발생률']),
            ('면적 기반 조건부 추정', flux, 'g', '22.28331621 cm² × 240 µg/cm² ÷ 1000000 ÷ 0.02',
             '한 면 전체 웨이브 도포 시 제조사 범위 상단을 선택해 소수점 4자리 올림. 침착량이지 탱크 출고량은 아님. 실납 내부 플럭스와 다른 자재.',
             ['웨이브 공정 선택 여부', '분사 전달 손실·증발·초기 충전량']),
            ('세정 투입 기준 필요', None, 'mL', '회당 실측 투입 mL × 대당 세정 횟수 (회수량 별도)',
             '한 면 22.28331621 cm², 양면 44.56663242 cm². 제조사 밀도 0.785–0.786 g/mL는 질량/부피 환산 근거이며 면적당 도포량 규격이 아님.',
             ['세정 필요 여부와 방식', '와이프 흡수량·증발·회수량을 포함한 회당 투입량']),
            ('납조 보충 기준 필요', None, 'g', '접합부 납 체적 mm³ × 7.37 g/cm³ ÷ 1000 + 대당 드로스 손실',
             'PCB 두께 1.6 mm. 드릴 구멍 전체 체적에는 핀과 열패드 비아가 포함되므로 그대로 납 사용량으로 쓰지 않음. 수삽 접합을 실납/납조에 중복 배분하지 않음.',
             ['웨이브/선택납땜 공정 선택', '핀 치수·필렛 형상·납조 잔량과 드로스 손실']),
        ]
        for m, (status, base, unit, formula, basis, missing) in zip(ref['materials'], descriptions):
            material_rows.append(dict(material_id=m['id'], name=m['name'], unit=unit, status=status,
                base_per_board=base, production_approved=False, origin='derived_conditional' if base else 'incomplete',
                formula=formula, basis=basis, missing=missing, evidence=m['evidence'] + c['evidence'],
                default_allowance_percent='10', allowance_basis='10%는 조정 가능한 초기 계획 여유율이며 제조사 권고·실측 손실률 아님.',
                setup_quantity=None, setup_basis='초기 비드/장비 충전/회수 잔량 미확인; 0으로 추정하지 않음'))
        xs, ys = zip(*[(Decimal(x), Decimal(y)) for x, y in c['outline']['vertices_mm']])
        configs.append(dict(configuration_id=cid, board=dict(width_mm=format((max(xs)-min(xs)).normalize(), 'f'),
            length_mm=format((max(ys)-min(ys)).normalize(), 'f'),
            thickness_mm=child(child(board, 'general'), 'thickness')[1], area_mm2=c['outline']['area_mm2'],
            area_cm2=c['outline']['area_cm2'], source_id=source['id']), paste_geometry=geo,
            geometry_evidence=dict(id='evidence:process-usage:v1:' + rev + ':paste-union',
                source_id=source['id'], origin='derived',
                locator='module pad layers F.Paste/B.Paste and fp_line paste graphics; see paste_geometry.by_reference',
                description='BOM population-filtered PCB paste union; conditional stencil estimate, not measured consumption'),
            materials=material_rows))
    return dict(format='process-per-board-basis/1.0', version=1,
        reference_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        source_commit=ref['existing_catalog']['source_commit'], sources=ref['sources'],
        catalog=ref['existing_catalog'], configurations=configs,
        policy=dict(random_values=False, unknown_is_zero=False, national_certification_claimed=False,
                    measured_consumption=False, fixed_setup_included=False,
                    material_routes_are_candidates=True),
        generator='ontologics.production_planning.build_usage_basis; Shapely 2.1.2')


if __name__ == '__main__':
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2) + '\n')
    print(OUTPUT)
