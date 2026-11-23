FROM node:22-bookworm-slim AS canvas-dependencies
WORKDIR /runtime
COPY deploy/canvas/package*.json ./
RUN npm ci --omit=dev --no-audit --no-fund \
    && rm -rf node_modules/@next/swc-linux-*-musl \
        node_modules/@img/sharp-linuxmusl-* node_modules/@img/sharp-libvips-linuxmusl-* \
    && node -e 'require("next"); require("sharp"); if (["x64", "arm64"].includes(process.arch)) require("@next/swc-linux-" + process.arch + "-gnu")'

FROM python:3.12-slim-bookworm AS canvas-assets
WORKDIR /app
COPY artifacts/ artifacts/
COPY scripts/restore_canvas.py scripts/restore_canvas.py
RUN python scripts/restore_canvas.py --skip-node-dependencies

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_DATA_DIR=/data \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PLAYWRIGHT_NODEJS_PATH=/usr/local/bin/node \
    NODE_ENV=production
WORKDIR /app
COPY --from=canvas-dependencies /usr/local/bin/node /usr/local/bin/node
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates fonts-dejavu-core fonts-wqy-zenhei libatomic1 supervisor \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./
COPY work/yingliu-studio/requirements.txt work/yingliu-studio/requirements.txt
COPY work/research-jimeng2api/requirements*.txt work/research-jimeng2api/
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-compile -r requirements.txt \
    && python -c 'from pathlib import Path; import playwright; (Path(playwright.__file__).parent / "driver" / "node").unlink(missing_ok=True)' \
    && ln -s "$(python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" /usr/local/bin/ffmpeg \
    && ffmpeg -hide_banner -loglevel error -f lavfi -i color=size=64x64 -frames:v 1 -threads 1 -c:v libx264 -f null - \
    && python -m playwright --version
RUN python -m playwright install --with-deps chromium --only-shell \
    && rm -rf /var/lib/apt/lists/*
COPY --from=canvas-assets --chown=10001:10001 /app/work/infinite-inspect/resources/app/web work/infinite-inspect/resources/app/web
COPY --from=canvas-dependencies /runtime/node_modules work/infinite-inspect/resources/app/web/node_modules
COPY work/yingliu-studio/backend work/yingliu-studio/backend
COPY work/yingliu-studio/web work/yingliu-studio/web
COPY work/research-jimeng2api/app work/research-jimeng2api/app
COPY work/research-jimeng2api/web work/research-jimeng2api/web
COPY deploy/ deploy/
RUN useradd --uid 10001 --create-home studio \
    && mkdir -p /data \
    && chown studio:studio /data
USER studio
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=15s --start-period=60s --retries=3 \
    CMD ["python", "/app/deploy/healthcheck.py"]
CMD ["python", "/app/deploy/entrypoint.py"]
