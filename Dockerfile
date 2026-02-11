# Arena2API - Docker Image
# 纯 Python 服务器，无浏览器依赖，镜像极小
FROM python:3.11-slim

LABEL maintainer="arena2api"
LABEL description="Arena.ai to OpenAI API Proxy - Multi-Account Edition"

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制服务器代码
COPY server.py .

# 默认端口
EXPOSE 9090

# 环境变量（可在 docker-compose 或 docker run 中覆盖）
ENV PORT=9090
ENV DEBUG=""
ENV API_KEYS=""
ENV EXTENSION_SECRET=""
ENV TOKEN_POOL_MAX=30
ENV TOKEN_EXPIRY_MS=110000
ENV PROFILE_TIMEOUT_S=120
ENV PROMPT_DEBUG=""

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')" || exit 1

# 启动
CMD ["python", "server.py"]