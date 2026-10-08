from sqlalchemy import select
from backend.app import models as m
from backend.app.services.capture.safety import validate_url,validate_selector,validate_recipe
def intake(s,change):
    missing=[]
    if not change.target_url:missing.append('Target URL')
    else:
        try:validate_url(change.target_url)
        except ValueError as e:missing.append(str(e))
    sections=s.scalars(select(m.Section).filter_by(change_request_id=change.id)).all()
    if not sections:missing.append('Changed sections with CSS selectors')
    for section in sections:
        try:validate_selector(section.selector)
        except ValueError as e:missing.append(str(e))
        if not section.design_spec_id or not s.get(m.Spec,section.design_spec_id):missing.append('Linked design spec for '+section.name)
    if not change.viewport_ids:missing.append('At least one viewport or device profile')
    elif any(not s.get(m.Viewport,id) for id in change.viewport_ids):missing.append('Unknown viewport profile')
    recipes=s.scalars(select(m.Recipe).filter_by(change_request_id=change.id)).all()
    if not recipes:missing.append('Capture recipe')
    for recipe in recipes:
        try:validate_recipe(recipe.steps_json,recipe.allow_cart_interaction)
        except ValueError as e:missing.append('Recipe broken: '+str(e))
    return {'complete':not missing,'missing':list(dict.fromkeys(missing))}
