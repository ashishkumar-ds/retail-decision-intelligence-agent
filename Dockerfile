# Retail Decision Intelligence Agent — production image
# Standard, boring, reproducible: slim base, non-root user, pinned deps.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8001

WORKDIR /srv/app

# Install dependencies first (layer cache: rebuilds only when deps change).
COPY pyproject.toml README.md ./
COPY app ./app
COPY approvals ./approvals
COPY decision_engine ./decision_engine
COPY guardrails ./guardrails
COPY memory ./memory
COPY phase2 ./phase2
COPY presentation ./presentation
COPY rag ./rag
COPY tools ./tools
# Editable install: the RAG corpus is rebuilt at startup from
# rag/sources/*.md resolved relative to the package file, so the package
# must be imported from this source tree, not site-packages.
RUN pip install --no-cache-dir -e .

# Unprivileged runtime user; logs volume is writable by this uid.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /srv/app/logs \
    && chown -R agent:agent /srv/app
USER agent

EXPOSE 8001

# Container-native healthcheck: the endpoint must report ok without curl.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8001\")}/health',timeout=4)" || exit 1

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
