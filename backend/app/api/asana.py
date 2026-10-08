from fastapi import APIRouter
from backend.app.db import session,all_rows
from backend.app import models as m
router=APIRouter(prefix='/api/asana',tags=['Asana'])
@router.get('/mock/board')
def board():
    with session() as s:return {'mode':'mock','projects':all_rows(s,m.Project),'sections':all_rows(s,m.AsanaSection),'tasks':all_rows(s,m.Task),'stories':all_rows(s,m.Story),'attachments':all_rows(s,m.Attachment),'custom_fields':all_rows(s,m.CustomField)}
