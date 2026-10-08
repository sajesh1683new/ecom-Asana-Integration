import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
DATA = Path(os.getenv('DATA_DIR','data')).resolve()
DATA.mkdir(parents=True,exist_ok=True)
ORIGIN = os.getenv('DEMO_ORIGIN','http://127.0.0.1:8000').rstrip('/')
ROLES = ['Requester / Product owner','Designer','QA reviewer','QA lead','Developer','Release owner']
