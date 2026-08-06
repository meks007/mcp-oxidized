FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/

RUN pip install --no-cache-dir "mcp[cli]>=1.0.0" httpx hatchling && \
    pip install --no-cache-dir .

ENV OXIDIZED_URL=""
ENV OXIDIZED_USER=""
ENV OXIDIZED_PASS=""
ENV MCP_PORT="8000"

EXPOSE 8000

CMD ["python", "-m", "mcp_oxidized.server"]
