from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
app=FastAPI(title='Visual QA Bridge',version='0.1')
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173'],allow_methods=['*'],allow_headers=['Content-Type','X-Role'])
@app.get('/api/health',tags=['System'])
def health(): return {'status':'ok','application':'Visual QA Bridge'}
from backend.app.api.asana import router as asana_router
app.include_router(asana_router)
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
app.mount('/demo-store/assets',StaticFiles(directory='backend/app/demo'),name='store-assets')
@app.get('/demo-store/{path:path}',include_in_schema=False)
def storefront(path: str): return FileResponse('backend/app/demo/index.html')
from backend.app.api.changes import router as changes_router
from fastapi.responses import JSONResponse
from fastapi import Request
app.include_router(changes_router)
from backend.app.api.captures import router as captures_router
app.include_router(captures_router)
from backend.app.api.findings import router as findings_router
app.include_router(findings_router)
from backend.app.config import DATA
for folder in ('designs','screenshots'):
    (DATA/folder).mkdir(exist_ok=True)
    app.mount('/files/'+folder,StaticFiles(directory=str(DATA/folder)),name='local-'+folder)
@app.exception_handler(ValueError)
async def invalid(request: Request,exc: ValueError):return JSONResponse(status_code=422,content={'detail':str(exc)})
