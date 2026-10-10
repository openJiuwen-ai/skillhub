# Worker Pod image: agent-core LOCAL mode + skill-runner worker HTTP API.
# Build from skillhub repo root: docker build -f docker/skill-agent-worker/Dockerfile -t skill-agent-worker:latest .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# 依赖镜像源不内置默认值，构建时显式指定：--build-arg PIP_INDEX=...（tuna 偶发返回截断 metadata，不建议）
ARG PIP_INDEX
ARG PIP_TRUSTED_HOST

# worker 运行时依赖 + openjiuwen agent-core
# [observability]: swarm team 默认挂 core.observability rail，需 opentelemetry exporters
RUN pip install -i ${PIP_INDEX:?请通过 --build-arg PIP_INDEX=... 指定 PyPI 镜像源} \
        ${PIP_TRUSTED_HOST:+--trusted-host ${PIP_TRUSTED_HOST}} \
        fastapi "uvicorn[standard]" httpx pydantic python-dotenv aiosqlite \
        "openjiuwen[observability]==0.1.15" \
    && python3 -c "import opentelemetry.sdk, opentelemetry.exporter.otlp.proto.grpc.trace_exporter, opentelemetry.exporter.otlp.proto.http.trace_exporter; print('otel sdk + exporters OK')"

RUN pip install -i ${PIP_INDEX:?请通过 --build-arg PIP_INDEX=... 指定 PyPI 镜像源} \
        ${PIP_TRUSTED_HOST:+--trusted-host ${PIP_TRUSTED_HOST}} \
        pyarrow \
        "scikit-learn>=1.0" \
        scipy \
        matplotlib \
        tabulate \
        python-pptx \
        jieba

# Preload tiktoken vocabularies to avoid ~120s network block at first LLM request.
ENV TIKTOKEN_CACHE_DIR=/app/tiktoken_cache
RUN mkdir -p /app/tiktoken_cache \
    && python3 -c "import tiktoken; [tiktoken.get_encoding(n) for n in ('cl100k_base','o200k_base')]" \
    && chmod -R 0777 /app/tiktoken_cache

# skill_runner 包（worker 代码）
COPY skill-runner /app/skill_runner
RUN rm -rf /app/skill_runner/.venv /app/skill_runner/__pycache__

RUN useradd --system --uid 10001 --create-home --home-dir /home/skillworker skillworker \
    && mkdir -p /workspace \
    && chown -R 10001:10001 /workspace /home/skillworker \
    && chmod 0777 /workspace

USER 10001
WORKDIR /workspace
ENV PYTHONPATH=/app \
    HOME=/workspace \
    SKILL_RUNNER_EXECUTOR=local \
    SKILL_RUNNER_WORKSPACE_ROOT=/workspace

COPY docker/skill-agent-worker/entrypoint.sh /entrypoint.sh
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
