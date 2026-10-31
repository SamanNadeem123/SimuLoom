FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.30 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

RUN useradd --create-home --uid 10001 simuloom

WORKDIR /app

COPY pyproject.toml uv.lock alembic.ini ./
COPY src ./src
COPY alembic ./alembic

RUN uv sync --frozen --no-dev

ENV PATH="/opt/venv/bin:$PATH"

USER simuloom
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn simuloom.api.app:create_app --factory --host 0.0.0.0 --port 8000"]
