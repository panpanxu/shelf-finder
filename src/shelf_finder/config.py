import os
from pathlib import Path

# 内网 vLLM 服务不校验 key，openai SDK 要求非空，按 vLLM 约定传 EMPTY
LLM_BASE_URL = os.environ.get("SHELF_LLM_BASE_URL", "http://192.168.0.88:8888/v1")
LLM_MODEL = os.environ.get("SHELF_LLM_MODEL", "DeepSeek-v4.1-Flash-EXL3")
LLM_API_KEY = os.environ.get("SHELF_LLM_API_KEY", "EMPTY")

DATA_DIR = Path(os.environ.get("SHELF_DATA_DIR", "data"))

# douban-sync 用的命令行工具，需要 Node.js 22.13 以上；全局装了就改成 "douban"
DOUBAN_CLI = os.environ.get("SHELF_DOUBAN_CLI", "npx -y @marvae24/douban-cli")

# 设为 off 时不提供上传和重新识别，只查看、搜索和手工维护已有数据，不需要装 OCR 依赖
RECOGNITION = os.environ.get("SHELF_RECOGNITION", "on") != "off"
