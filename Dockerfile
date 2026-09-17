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
# Default documentation/exposure port.
EXPOSE 10000

CMD ["python", "-m", "finstack.server", "--transport", "streamable-http"]
