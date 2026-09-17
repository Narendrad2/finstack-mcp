FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
COPY src/ src/
COPY README.md .
COPY LICENSE .

RUN pip install --no-cache-dir -e ".[hosted]"

# Runtime environment
ENV FINSTACK_HOST=0.0.0.0
ENV FINSTACK_TRANSPORT=streamable-http
ENV FINSTACK_LOG_LEVEL=INFO

# Render supplies PORT at runtime.
EXPOSE 10000

# Container health check.
# Render itself will supply the actual PORT environment variable.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os, urllib.request; port=os.getenv('PORT', '10000'); urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=4)" || exit 1

CMD ["python", "-m", "finstack.server", "--transport", "streamable-http"]
