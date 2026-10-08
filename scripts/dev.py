import os
import subprocess
import sys
import time
import shutil
from backend.app.db import migrate


def main():
    migrate()
    host = os.getenv("DEV_HOST", "127.0.0.1")
    children = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                host,
                "--port",
                "8000",
            ]
        )
    ]
    children.append(subprocess.Popen([sys.executable, "-m", "backend.app.worker"]))
    node = shutil.which("node") or "node"
    children.append(
        subprocess.Popen([node, "node_modules/vite/bin/vite.js", "--host", host], cwd="frontend")
    )
    try:
        while all(c.poll() is None for c in children):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        for c in children:
            c.terminate()


if __name__ == "__main__":
    main()
