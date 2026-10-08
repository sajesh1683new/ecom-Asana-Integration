import subprocess, sys, time, shutil
from backend.app.db import migrate
def main():
    migrate()
    children=[subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port','8000'])]
    node=shutil.which('node') or 'node'
    children.append(subprocess.Popen([node,'node_modules/vite/bin/vite.js','--host','127.0.0.1'],cwd='frontend'))
    try:
        while all(c.poll() is None for c in children):time.sleep(1)
    except KeyboardInterrupt:pass
    finally:
        for c in children:c.terminate()
if __name__=='__main__':main()
