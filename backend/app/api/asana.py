from fastapi import APIRouter,Depends
from backend.app.db import session,all_rows
from backend.app import models as m
router=APIRouter(prefix='/api/asana',tags=['Asana'])
@router.get('/mock/board')
def board():
    with session() as s:return {'mode':'mock','projects':all_rows(s,m.Project),'sections':all_rows(s,m.AsanaSection),'tasks':all_rows(s,m.Task),'stories':all_rows(s,m.Story),'attachments':all_rows(s,m.Attachment),'custom_fields':all_rows(s,m.CustomField)}
from backend.app.api.deps import current_role,require
from backend.app.services.asana_sync import pull
@router.post('/pull')
def pull_tasks(role: str=Depends(current_role)):
    require(role,'QA lead','Requester / Product owner')
    with session() as s:
        result=pull(s,role);s.commit();return {'imported':result}
