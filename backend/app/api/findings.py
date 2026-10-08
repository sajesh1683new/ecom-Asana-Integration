from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import session,all_rows,record,add
from backend.app.api.deps import require,current_role
from backend.app.api.changes import needed
from backend.app.services.events import event
from backend.app.services.workflow import transition
router=APIRouter(prefix='/api',tags=['Human review'])
class Triage(BaseModel):
    model_config=ConfigDict(extra='forbid')
    outcome: Literal['valid','not an issue','by design','out of scope']
    kind: Literal['defect','change_request','question']='defect'
    reason: str=Field(min_length=5,max_length=3000)
class Manual(BaseModel):
    model_config=ConfigDict(extra='forbid')
    change_request_id: int
    title: str=Field(min_length=5)
    description: str=Field(min_length=5)
    element: str=''
    viewport: str=''
    recipe_id: int=0
    kind: Literal['defect','change_request','question']='question'
class Transition(BaseModel):
    status: str
@router.get('/findings')
def findings(change_request_id: int):
    with session() as s:
        result=all_rows(s,m.Finding,change_request_id=change_request_id)
        for f in result:
            f['measurements']=[record(x) for x in s.scalars(select(m.Measurement).where(m.Measurement.screenshot_id.in_(select(m.Screenshot.id).where(m.Screenshot.capture_run_id==f['capture_run_id'],m.Screenshot.recipe_id==f['recipe_id'])),m.Measurement.selector==f['element'])) if x.computed_json.get('profile')==f['viewport']][:8]
        return result
@router.post('/findings')
def manual(body: Manual,role: str=Depends(current_role)):
    require(role,'QA reviewer','QA lead')
    with session() as s:
        change=needed(s,body.change_request_id)
        if change.status not in ('In human review','Approved for release'):raise HTTPException(409,'Manual findings can be added during human review, or reported as escaped after release.')
        f=add(s,m.Finding,**body.model_dump(),source='reviewer',created_by=role,escaped=change.status=='Approved for release')
        f.source_ref=f'M-{f.id}'
        matching=s.scalar(select(m.Finding).where(m.Finding.change_request_id==change.id,m.Finding.source.in_(['rule','ai']),m.Finding.element==body.element,m.Finding.viewport==body.viewport,m.Finding.recipe_id==body.recipe_id)) if body.element and body.viewport else None
        event(s,change.id,'manual',f.id,'','Created',role,{'matched':bool(matching),'escaped':f.escaped})
        if f.escaped:event(s,change.id,'escaped',f.id,'','Reported',role,{'reason':body.description})
        s.commit();return record(f)
@router.post('/findings/{id}/triage')
def triage(id: int,body: Triage,role: str=Depends(current_role)):
    require(role,'QA reviewer','QA lead')
    with session() as s:
        f=s.get(m.Finding,id)
        if not f:raise HTTPException(404,'Finding not found.')
        change=needed(s,f.change_request_id)
        if change.status!='In human review':raise HTTPException(409,'Start human review after the AI first pass before triaging findings.')
        if body.outcome=='valid' and body.kind=='defect' and (not f.element or not f.viewport):raise HTTPException(409,'Identify the element and viewport before confirming a defect. Original text remains unchanged.')
        old=f.triage_outcome;f.triage_outcome=body.outcome;f.kind=body.kind;f.status='Confirmed' if body.outcome=='valid' else 'Dismissed'
        event(s,change.id,'triage',id,old,body.outcome,role,{'source':f.source,'kind':body.kind,'reason':body.reason})
        s.commit();return record(f)
@router.post('/change-requests/{id}/transition')
def move(id: int,body: Transition,role: str=Depends(current_role)):
    with session() as s:
        change=needed(s,id);transition(s,change,body.status,role);s.commit();return record(change)
