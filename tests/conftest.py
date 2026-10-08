"""Isolated, disposable test database; never mutate the demo workspace."""

import os
import uuid
from pathlib import Path

os.environ["DATA_DIR"] = str(Path("data/tests", uuid.uuid4().hex).resolve())
os.environ["DEMO_ORIGIN"] = "http://127.0.0.1:8001"
os.environ["ASANA_MODE"] = "mock"
os.environ["AI_PROVIDER"] = "mock"
os.environ["LIVE_CAPTURE_ENABLED"] = "false"

import subprocess
import sys
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.seed import seed
from backend.app.main import app


@pytest.fixture(scope="session")
def client():
    seed()
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="session")
def storefront(client):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.app.main:app",
            "--port",
            "8001",
            "--no-access-log",
        ]
    )
    try:
        for _ in range(100):
            try:
                if httpx.get("http://127.0.0.1:8001/api/health").status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Test storefront failed to start")
        yield
    finally:
        process.terminate()
        process.wait(timeout=10)
