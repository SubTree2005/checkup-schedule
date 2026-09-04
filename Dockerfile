FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

COPY pyproject.toml README.md ./
COPY packages ./packages
RUN python -m pip install --no-cache-dir ".[backend]"

RUN addgroup --system app && adduser --system --ingroup app app && chown app:app /app
COPY --chown=app:app apps ./apps

USER app

EXPOSE 8080
CMD uvicorn apps.backend.checkup_backend.main:app --host 0.0.0.0 --port "${PORT}"

