FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages ./packages
RUN python -m pip install --no-cache-dir ".[backend]"

COPY apps ./apps

EXPOSE 8080
CMD uvicorn apps.backend.checkup_backend.main:app --host 0.0.0.0 --port "${PORT}"

