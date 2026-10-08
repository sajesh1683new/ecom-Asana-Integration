import os
from sqlalchemy.orm import Session
from backend.app.integrations.asana.base import AsanaClient
from backend.app.integrations.asana.mock_client import MockAsanaClient
from backend.app.integrations.asana.real_client import RealAsanaClient
def get_client(s: Session) -> AsanaClient:
    mode=os.getenv('ASANA_MODE','mock')
    if mode=='real':return RealAsanaClient()
    if mode!='mock':raise ValueError('ASANA_MODE must be mock or real.')
    return MockAsanaClient(s)
