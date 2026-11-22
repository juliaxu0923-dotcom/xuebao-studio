FROM node:22-bookworm-slim AS canvas-dependencies
WORKDIR /runtime
COPY deploy/canvas/package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    NODE_ENV=production
WORKDIR /app
COPY --from=canvas-dependencies /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates ffmpeg fonts-dejavu-core fonts-wqy-zenhei supervisor \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
COPY work/yingliu-studio/requirements.txt work/yingliu-studio/requirements.txt
COPY work/research-jimeng2api/requirements*.txt work/research-jimeng2api/
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
    && python -m playwright install --with-deps chromium --only-shell \
    && rm -rf /var/lib/apt/lists/*
COPY artifacts/ artifacts/
COPY scripts/restore_canvas.py scripts/restore_canvas.py
RUN python scripts/restore_canvas.py --skip-node-dependencies \
    && rm artifacts/infinite-canvas-0.8.9-web.zip
COPY --from=canvas-dependencies /runtime/node_modules work/infinite-inspect/resources/app/web/node_modules
COPY work/yingliu-studio/backend work/yingliu-studio/backend
COPY work/yingliu-studio/web work/yingliu-studio/web
COPY work/research-jimeng2api/app work/research-jimeng2api/app
COPY work/research-jimeng2api/web work/research-jimeng2api/web
COPY deploy/ deploy/
RUN useradd --uid 10001 --create-home studio \
    && mkdir -p /data \
    && chown studio:studio /data \
    && chown -R studio:studio work/infinite-inspect/resources/app/web/.next
USER studio
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=15s --start-period=60s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]
CMD ["python", "/app/deploy/entrypoint.py"]
