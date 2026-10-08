import hashlib,json,os
from datetime import datetime,timedelta,timezone
from sqlalchemy import select
from backend.app import models as m
from backend.app.db import add,record
from backend.app.integrations.asana.factory import get_client
from backend.app.services.events import status,event
from backend.app.services.intake import intake
import threading
SYNC_LOCK=threading.Lock()

def pull(s,role: str):
    client=get_client(s);tasks=client.list_tasks(os.getenv('ASANA_PROJECT_GID','1000'));imported=[]
    for task in tasks:
        if task.get('parent_gid') or task.get('parent') or task.get('is_template'):continue
        if s.scalar(select(m.Change).filter_by(asana_gid=task['gid'])):continue
        fields=task.get('custom_fields',{})
        if isinstance(fields,list):
            fields={f['name']:f.get('text_value') or f.get('display_value') or [o['name'] for o in f.get('multi_enum_values',[])] for f in fields}
            for key in ('Changed Sections','Viewports'):
                if isinstance(fields.get(key),str):
                    try:fields[key]=json.loads(fields[key])
                    except ValueError:fields[key]=[]
        title=task['name'];code=title.split(' ')[0]
        change=add(s,m.Change,asana_gid=task['gid'],code=code,title=title,description=task.get('notes',''),target_url=fields.get('Target URL',''),requester_id=1,designer_id=2,qa_lead_id=6,viewport_ids=[v.id for v in s.scalars(select(m.Viewport)) if v.name in (fields.get('Viewports') or [])])
        event(s,change.id,'change',change.id,'','Draft',role,{'source':'Asana import'})
        for section in fields.get('Changed Sections',[]):
            linked=int(fields.get('Design Spec') or 0) if str(fields.get('Design Spec','')).isdigit() else 0
            add(s,m.Section,change_request_id=change.id,name=section['name'],selector=section['selector'],design_spec_id=linked)
        for recipe in fields.get('recipes',[]):add(s,m.Recipe,change_request_id=change.id,name=recipe['name'],steps_json=recipe['steps'],allow_cart_interaction=recipe.get('allow_cart_interaction',False))
        for story in client.list_comments(task['gid']):
            source=story.get('source_ref','')
            if source not in ('F1','F2','F3','F4','F5','F6'):continue
            add(s,m.Finding,change_request_id=change.id,source='imported_comment',source_ref=source,title=story['text'],description=story['text'],created_by=story.get('author','Reviewer'),kind='change_request' if source in ('F3','F5') else 'question',element='' if source in ('F2','F4') else {'F1':'[data-testid=popup-header]','F3':'[data-testid=timer-label]','F5':'[data-testid=popup-divider]','F6':'[data-testid=feature-list-premium]'}[source],viewport='mobile-390' if source in ('F1','F2') else '')
        check=intake(s,change);change.intake_complete=check['complete'];status(s,change,'Ready for QA' if check['complete'] else 'Intake incomplete',role,check)
        if not check['complete']:client.add_comment(task['gid'],'[Visual QA Bridge intake] Please complete: '+', '.join(check['missing'])+'. Returned to Requester / Product owner.')
        if code=='CHG-099':
            stamp=datetime.now(timezone.utc)-timedelta(days=14);change.status='Approved for release'
            for offset,state in [(0,'Draft'),(20,'Ready for QA'),(24,'Capturing'),(26,'AI first pass done'),(27,'In human review'),(50,'Queue consolidated'),(55,'Synced to Asana'),(65,'Fix in progress'),(75,'Recheck'),(84,'Approved for release')]:add(s,m.Event,entity_type='change',entity_id=change.id,change_request_id=change.id,to_state=state,actor_role='Release owner' if state=='Approved for release' else 'QA reviewer',timestamp=(stamp+timedelta(minutes=offset)).isoformat(),detail={'synthetic_history':True})
            f=add(s,m.Finding,change_request_id=change.id,source='reviewer',source_ref='ESC-1',title='Cart drawer clipping on a physical device',description='Synthetic escaped defect reported one week after approval.',element='[data-testid=cart-drawer]',viewport='iPhone 13',kind='defect',status='Confirmed',triage_outcome='valid',escaped=True,created_by='QA reviewer',created_at=(stamp+timedelta(days=7)).isoformat())
            event(s,change.id,'escaped',f.id,'','Reported','QA reviewer',{'synthetic':True})
        if code=='CHG-103':
            change.status='In human review'
            for outcome,title in [('valid','Navigation link overlaps at mobile width'),('not an issue','Shadow looks too strong')]:
                f=add(s,m.Finding,change_request_id=change.id,source='ai',source_ref='SEED-'+outcome,title=title,description='Synthetic already-triaged history',element='[data-testid=navigation]',viewport='mobile-390',triage_outcome=outcome,status='Confirmed' if outcome=='valid' else 'Dismissed',kind='defect')
                event(s,change.id,'triage',f.id,'',outcome,'QA reviewer',{'source':'ai','synthetic_history':True})
        imported.append(record(change))
    add(s,m.SyncLog,direction='pull',entity='project',payload_hash=hashlib.sha256(json.dumps(tasks,sort_keys=True).encode()).hexdigest(),result='success',detail={'imported':len(imported)})
    return imported

def push(s,change,role: str):
    from backend.app.services.consolidation import populate
    if change.status not in ('Queue consolidated','Synced to Asana','Recheck','Release blocked'):raise ValueError('Sync follows queue consolidation and QA lead approval.')
    if s.scalar(select(m.Group).filter_by(change_request_id=change.id,state='possible')):raise ValueError('Resolve each possible grouping in Check the AI before syncing.')
    rows=populate(s,change.id);client=get_client(s)
    payload=[{'id':q.id,'source_ids':q.source_ids,'status':q.status,'summary':q.issue_summary,'owner':q.accountable_role} for q in rows]
    digest=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
    previous=s.scalar(select(m.SyncLog).filter_by(direction='push',entity=str(change.id),payload_hash=digest,result='success'))
    if previous:return {'message':'Already synced; no duplicate comments or subtasks.','log_id':previous.id}
    log=add(s,m.SyncLog,direction='push',entity=str(change.id),payload_hash=digest,result='pending',detail={'approved_by':role})
    try:
        summary='[Visual QA Bridge] Human-reviewed summary\n'+'\n'.join(f"{', '.join(q.source_ids)} | {q.status} | {q.issue_summary} | Owner: {q.accountable_role}" for q in rows)+f'\n[VQA-SYNC:{digest}]'
        comments=client.list_comments(change.asana_gid)
        if not any(f'[VQA-SYNC:{digest}]' in x.get('text','') for x in comments):client.add_comment(change.asana_gid,summary)
        subtasks=client.list_subtasks(change.asana_gid);created=[];attached=set()
        for q in rows:
            f=s.get(m.Finding,q.finding_id)
            if not f:continue
            marker=f'[VQA-DEFECT:{change.id}:{q.id}]'
            existing=next((t for t in subtasks if marker in t.get('notes','')),None)
            if q.status=='Closed' and existing:
                client.update_task(existing['gid'],completed=True);continue
            if q.status not in ('Ready for fix','Fixed: awaiting recheck','Reopened') or f.triage_outcome!='valid' or f.kind!='defect':continue
            if not existing:
                task=client.create_subtask(change.asana_gid,name='[QA defect] '+q.issue_summary[:100],notes=f"{marker}\nSources: {', '.join(q.source_ids)}\nHuman-confirmed defect.\n{f.description}\nElement: {f.element}\nViewport: {f.viewport}\nEvidence is attached where permitted; localhost paths are not public.",assignee_role='Developer')
                subtasks.append(task);created.append(task['gid'])
            shot=s.get(m.Screenshot,f.evidence_screenshot_id)
            if not shot and q.evidence_ids:
                evidence=s.get(m.Finding,q.evidence_ids[0]);shot=s.get(m.Screenshot,evidence.evidence_screenshot_id) if evidence else None
            if shot and shot.id not in attached:
                attachment_hash=hashlib.sha256((change.asana_gid+shot.sha256).encode()).hexdigest()
                if not s.scalar(select(m.SyncLog).filter_by(direction='attachment',payload_hash=attachment_hash,result='success')):
                    try:
                        client.add_attachment(change.asana_gid,shot.file_path)
                        add(s,m.SyncLog,direction='attachment',entity=str(shot.id),payload_hash=attachment_hash,result='success')
                    except ValueError as exc:add(s,m.SyncLog,direction='attachment',entity=str(shot.id),payload_hash=attachment_hash,result='error',detail={'message':str(exc)})
                attached.add(shot.id)
        if os.getenv('ASANA_MODE','mock')=='mock':
            client.set_custom_field(change.asana_gid,'2000','Fixes' if any(q.status=='Ready for fix' for q in rows) else 'In Review')
            client.update_task(change.asana_gid,section_gid='1004' if any(q.status=='Ready for fix' for q in rows) else '1003')
        else:
            task=client.get_task(change.asana_gid)
            field=next((f for f in task.get('custom_fields',[]) if f.get('name')=='QA Status'),None)
            option=next((x for x in (field or {}).get('enum_options',[]) if x['name']=='In Review'),None)
            if field and option:client.set_custom_field(change.asana_gid,field['gid'],option['gid'])
            else:client.add_comment(change.asana_gid,'[Visual QA Bridge managed status] QA Status: In Review. Custom field unavailable; using comment fallback.')
        log.result='success';log.detail={'approved_by':role,'created_subtasks':created,'all_finding_ids':[q.finding_id for q in rows]}
        status(s,change,'Synced to Asana',role,{'sync_log_id':log.id})
        return {'message':'Reviewed findings written back to '+os.getenv('ASANA_MODE','mock')+' Asana.','created_subtasks':created,'log_id':log.id}
    except Exception as exc:
        log.result='error';log.detail={'message':str(exc) if isinstance(exc,ValueError) else 'Asana write failed; refresh before retrying.'}
        s.commit();raise
