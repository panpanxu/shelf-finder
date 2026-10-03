# 只查看、搜索和手工维护已有数据的镜像，不装 OCR，镜像很小。
# 在仓库根目录构建：docker build -f deploy/Dockerfile -t shelf-finder:viewer .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-default-groups --no-editable

ENV SHELF_RECOGNITION=off \
    SHELF_DATA_DIR=/data \
    SHELF_PORT=8000
EXPOSE 8000
VOLUME ["/data"]

CMD ["/app/.venv/bin/shelf-finder"]
