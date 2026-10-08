from typing import Any
from backend.app.db import add,now
from backend.app import models as m
def event(s,change_id: int,kind: str,entity_id: int,old: str,new: str,role: str,detail: Any=None):
    return add(s,m.Event,change_request_id=change_id,entity_type=kind,entity_id=entity_id,from_state=old,to_state=new,actor_role=role,detail=detail or {})
def status(s,change,new: str,role: str,detail: Any=None):
    old=change.status;change.status=new;change.updated_at=now()
    event(s,change.id,'change',change.id,old,new,role,detail)
