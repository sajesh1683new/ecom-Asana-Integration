from sqlalchemy import select
from backend.app import models as m
from backend.app.services.events import status

def untriaged(s,change_id: int):
    return list(s.scalars(select(m.Finding).where(m.Finding.change_request_id==change_id,m.Finding.source.in_(['ai','rule']),m.Finding.triage_outcome=='')))
def transition(s,change,target: str,role: str):
    allowed={('AI first pass done','In human review'):['QA reviewer','QA lead'],('Synced to Asana','Fix in progress'):['Developer'],('Release blocked','In human review'):['QA reviewer','QA lead'],('In human review','Intake incomplete'):['QA lead']}
    roles=allowed.get((change.status,target),[])
    if role not in roles:raise ValueError(f'Cannot move from {change.status} to {target} as {role}. Use the dedicated capture, consolidation, sync or release action for that stage.')
    status(s,change,target,role)
