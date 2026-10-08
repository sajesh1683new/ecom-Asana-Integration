import os
from pathlib import Path
from typing import Any
import httpx

class RealAsanaClient:
    def __init__(self):
        if os.getenv('ASANA_MODE','mock')!='real':raise ValueError('Real Asana mode is not enabled.')
        token=os.getenv('ASANA_PAT','')
        if not token:raise ValueError('ASANA_PAT is missing. Use mock mode or configure the token locally.')
        self.client=httpx.Client(base_url='https://app.asana.com/api/1.0/',headers={'Authorization':'Bearer '+token},timeout=30)
    def request(self,method: str,path: str,**kwargs: Any) -> Any:
        try:r=self.client.request(method,path,**kwargs)
        except httpx.HTTPError:raise ValueError('Asana connection failed. Refresh before retrying an uncertain write.') from None
        if r.status_code==429:raise ValueError('Asana rate limit. Retry after '+r.headers.get('Retry-After','60')+' seconds.')
        if r.status_code in (401,403):raise ValueError('Asana token or permission does not allow this operation.')
        if r.is_error:raise ValueError(f'Asana returned HTTP {r.status_code}.')
        return r.json()
    def listing(self,path: str,**params: Any) -> list[dict[str,Any]]:
        out=[];params['limit']=100
        while True:
            body=self.request('GET',path,params=params);out.extend(body['data'])
            if not body.get('next_page'):return out
            params['offset']=body['next_page']['offset']
    def list_projects(self) -> list[dict[str,Any]]:return self.listing('projects',opt_fields='gid,name')
    def list_tasks(self,project_gid: str) -> list[dict[str,Any]]:
        return self.listing(f'projects/{project_gid}/tasks',opt_fields='gid,name,notes,custom_fields,modified_at,parent,completed')
    def get_task(self,gid: str) -> dict[str,Any]:return self.request('GET',f'tasks/{gid}',params={'opt_fields':'gid,name,notes,custom_fields,modified_at'})['data']
    def create_task(self,**values: Any) -> dict[str,Any]:return self.request('POST','tasks',json={'data':values})['data']
    def update_task(self,gid: str,**values: Any) -> dict[str,Any]:return self.request('PUT',f'tasks/{gid}',json={'data':values})['data']
    def list_comments(self,gid: str) -> list[dict[str,Any]]:return self.listing(f'tasks/{gid}/stories',opt_fields='gid,text,created_by.name')
    def list_subtasks(self,gid: str) -> list[dict[str,Any]]:return self.listing(f'tasks/{gid}/subtasks',opt_fields='gid,name,notes,completed')
    def add_comment(self,gid: str,text: str) -> dict[str,Any]:return self.request('POST',f'tasks/{gid}/stories',json={'data':{'text':text}})['data']
    def add_attachment(self,gid: str,file_path: str) -> dict[str,Any]:
        with Path(file_path).open('rb') as f:return self.request('POST','attachments',data={'parent':gid},files={'file':(Path(file_path).name,f,'image/png')})['data']
    def create_subtask(self,parent_gid: str,**values: Any) -> dict[str,Any]:
        role=values.pop('assignee_role','Developer')
        values['notes']=values.get('notes','')+f'\nAccountable role: {role}. Assign the appropriate person in Asana.'
        return self.request('POST',f'tasks/{parent_gid}/subtasks',json={'data':values})['data']
    def set_custom_field(self,gid: str,field_gid: str,value: Any) -> dict[str,Any]:return self.update_task(gid,custom_fields={field_gid:value})
