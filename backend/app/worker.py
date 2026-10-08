import os,time
from sqlalchemy import select,delete,text
from backend.app import models as m
from backend.app.db import session,migrate,now
from backend.app.config import DATA
from backend.app.services.capture.runner import capture
from backend.app.services.events import status

def run_once() -> bool:
    with session() as s:
        s.execute(text('BEGIN IMMEDIATE'))
        job=s.scalar(select(m.Job).where(m.Job.status=='Queued').order_by(m.Job.id))
        if not job:s.rollback();return False
        job.status='Running';job.attempts+=1;job_id=job.id;s.commit()
    started=time.perf_counter()
    with session() as s:
        job=s.get(m.Job,job_id);run=s.get(m.Run,job.payload_json['run_id']);change=s.get(m.Change,run.change_request_id)
        run.status='Running';s.commit()
        try:
            capture(s,run,run.scope_json)
            from backend.app.services.capture.rules import analyse
            from backend.app.services.capture.visual_diff import analyse_visual
            if not run.scope_json.get('test_recipe'):
                analyse(s,run);analyse_visual(s,run)
                if not run.scope_json.get('recheck_queue_ids'):
                    from backend.app.services.capture.ai_review import analyse_ai
                    analyse_ai(s,run)
                else:
                    from backend.app.services.recheck import finish_recheck
                    finish_recheck(s,run)
            run.status='Completed';run.finished_at=now();run.duration_ms=(time.perf_counter()-started)*1000
            job.status='Completed';status(s,change,'Recheck' if run.scope_json.get('recheck_queue_ids') else 'Ready for QA' if run.scope_json.get('test_recipe') else 'AI first pass done','QA reviewer',{'capture_complete':True,'run_id':run.id})
            s.commit()
        except Exception as exc:
            s.rollback();job=s.get(m.Job,job_id);run=s.get(m.Run,job.payload_json['run_id']);change=s.get(m.Change,run.change_request_id)
            message=str(exc) if isinstance(exc,ValueError) else f'{type(exc).__name__}: Recipe broken (timeout, missing selector or browser unavailable).'
            job.error=message;run.error=message;run.duration_ms+=(time.perf_counter()-started)*1000
            if job.attempts<3:job.status='Queued';run.status='Queued'
            else:
                job.status='Error';run.status='Error';run.finished_at=now();change.intake_complete=False
                status(s,change,'Intake incomplete','QA reviewer',{'reason':'Recipe broken','detail':message})
            s.commit()
    return True

def recover():
    with session() as s:
        for job in s.scalars(select(m.Job).filter_by(status='Running')):
            job.status='Queued' if job.attempts<3 else 'Error';job.error='Worker restarted after an abandoned attempt.'
            run=s.get(m.Run,job.payload_json['run_id']);run.status=job.status
            if job.status=='Error':status(s,s.get(m.Change,run.change_request_id),'Intake incomplete','QA reviewer',{'reason':'Recipe broken: abandoned worker job'})
        s.commit()
def main():
    migrate();lock=(DATA/'worker.lock').open('a+b');lock.seek(0);lock.write(b'0');lock.flush();lock.seek(0)
    if os.name=='nt':
        import msvcrt
        msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    recover();print('Visual QA Bridge worker ready',flush=True)
    while True:
        if not run_once():time.sleep(.5)
if __name__=='__main__':main()
