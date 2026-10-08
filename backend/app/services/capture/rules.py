import json
from collections import defaultdict
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import add

SOURCE_MAP={'SIDE_PADDING_MATCHES_SPEC':'F1','LINE_HEIGHT_MATCHES_SPEC':'F2','COPY_MATCHES_APPROVED':'F3','MIN_FONT_MOBILE':'F4','CONDITIONAL_VISIBILITY':'F5','ELEMENT_ORDER_MATCHES_SPEC':'F6'}
def analyse(s,run):
    shots={x.id:x for x in s.scalars(select(m.Screenshot).filter_by(capture_run_id=run.id))}
    values=s.scalars(select(m.Measurement).where(m.Measurement.screenshot_id.in_(list(shots)))).all()
    grouped=defaultdict(list)
    for value in values:
        shot=shots[value.screenshot_id];grouped[(shot.recipe_id,shot.viewport_profile_id)].append((value,shot))
    all_results=[]
    for (recipe_id,vp_id),data in grouped.items():
        profile=next(v for v in run.scope_json['viewports'] if v['id']==vp_id)
        specs={x['id']:x for x in run.scope_json['specs']}
        section_specs={x['selector']:specs.get(x['design_spec_id']) for x in run.scope_json['sections']}
        for rule in run.scope_json['rules']:
            code=rule['code']
            if code=='VISUAL_DIFF_VS_BASELINE':continue
            candidates=[]
            for value,shot in data:
                v=value.computed_json;testid=v.get('testid');parent=v.get('parent_selector',value.selector)
                if code=='MIN_FONT_MOBILE' and profile['width']<768 and testid=='feature-item':candidates.append((value,shot,parent))
                elif code=='SIDE_PADDING_MATCHES_SPEC' and testid=='popup-header':candidates.append((value,shot,parent))
                elif code=='LINE_HEIGHT_MATCHES_SPEC' and testid=='popup-headline':candidates.append((value,shot,parent))
                elif code=='COPY_MATCHES_APPROVED' and testid=='timer-label':candidates.append((value,shot,parent))
                elif code=='CONDITIONAL_VISIBILITY' and testid=='popup-divider':candidates.append((value,shot,parent))
                elif code=='ELEMENT_ORDER_MATCHES_SPEC' and testid=='feature-list-premium':candidates.append((value,shot,parent))
            seen=set()
            for value,shot,selector in candidates:
                if selector in seen:continue
                seen.add(selector);v=value.computed_json;spec_record=section_specs.get(selector)
                if not spec_record:continue
                spec=spec_record['spec_json'];expected=None;observed=None;passed=None
                if code=='MIN_FONT_MOBILE':
                    expected=rule['params_json'].get('minimum',spec.get('min_font_mobile'))
                    observed=min(c.computed_json['font_size'] for c,_,parent in candidates if parent==selector)
                    if expected is not None:passed=observed>=expected
                elif code=='SIDE_PADDING_MATCHES_SPEC':
                    expected=spec.get('padding');observed={'left':v['padding']['left'],'right':v['padding']['right']}
                    if expected is not None:passed=all(abs(n-expected)<=rule['params_json'].get('tolerance',2) for n in observed.values())
                elif code=='LINE_HEIGHT_MATCHES_SPEC':
                    expected=spec.get('line_height');observed=round(v['line_height']/v['font_size'],9) if v.get('line_height') else None
                    if observed is not None and expected is not None:passed=abs(observed-expected)<1e-8
                elif code=='COPY_MATCHES_APPROVED':
                    expected=spec.get('copy');observed=v['text_content']
                    if expected is not None:passed=observed==expected
                elif code=='ELEMENT_ORDER_MATCHES_SPEC':
                    expected=spec.get('order');observed=v['order']
                    if expected is not None:passed=observed==expected
                elif code=='CONDITIONAL_VISIBILITY':
                    body=next((c.computed_json for c,_ in data if c.computed_json.get('testid')=='popup-body'),None)
                    if body and spec.get('divider')=='scrollHeight > clientHeight':
                        expected=body['scrollHeight']>body['clientHeight'];observed={'visible':v['visible'],'scrollHeight':body['scrollHeight'],'clientHeight':body['clientHeight']};passed=observed['visible']==expected
                result=add(s,m.RuleResult,capture_run_id=run.id,recipe_id=recipe_id,viewport_profile_id=vp_id,selector=selector,rule_code=code,rule_version=rule['version'],spec_version=spec_record['version'],status='Blocked' if passed is None else 'Passed' if passed else 'Failed',expected=expected,observed=observed,screenshot_id=shot.id)
                all_results.append(result)
                if passed is False:
                    add(s,m.Finding,change_request_id=run.change_request_id,capture_run_id=run.id,source='rule',source_ref=f'R{run.id}-{result.id}',title=rule['description'],description=f"Measured {json.dumps(observed)}; expected {json.dumps(expected)}. Rule {code} v{rule['version']}, synthetic spec v{spec_record['version']}. Related source {SOURCE_MAP.get(code,'')} is not automatically confirmed.",element=selector,viewport=profile['name'],recipe_id=recipe_id,evidence_screenshot_id=shot.id,rule_code=code,kind='defect',created_by='Deterministic rules',expected=expected,observed=observed)
    return all_results
