FROM python:3.12-bookworm
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && playwright install --with-deps chromium
COPY frontend/package.json frontend/package.json
RUN cd frontend && npm install
COPY . .
CMD ["sh", "-c", "python -m backend.app.seed && python -m scripts.dev"]
