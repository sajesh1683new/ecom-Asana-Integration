from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
app=FastAPI(title='Visual QA Bridge',version='0.1')
app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173'],allow_methods=['*'],allow_headers=['Content-Type','X-Role'])
@app.get('/api/health',tags=['System'])
def health(): return {'status':'ok','application':'Visual QA Bridge'}
from backend.app.api.asana import router as asana_router
app.include_router(asana_router)
