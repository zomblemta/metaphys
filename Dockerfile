FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim-bookworm
RUN pip install --no-cache-dir uv==0.12.4
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend/app ./app
COPY backend/packages ./packages
COPY config.yaml /app/config.yaml
COPY --from=frontend /build/out /app/frontend/out
RUN useradd --uid 10001 --create-home metaphys && mkdir -p /app/var/charts && chown -R metaphys:metaphys /app/var
ENV PYTHONPATH=/app/backend:/app/backend/packages/harness PYTHONUNBUFFERED=1 PATH=/app/backend/.venv/bin:$PATH
USER metaphys
EXPOSE 8010
CMD ["uvicorn", "app.production:create_app", "--factory", "--host", "0.0.0.0", "--port", "8010", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*", "--timeout-graceful-shutdown", "10", "--no-access-log"]
