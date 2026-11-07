FROM python:3.14.3-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --locked --no-dev
COPY backend/app ./app
COPY backend/alembic.ini ./
COPY backend/migrations ./migrations
RUN useradd --uid 10001 --create-home appuser && mkdir -p /data/files && chown -R appuser:appuser /data /app
USER appuser
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
