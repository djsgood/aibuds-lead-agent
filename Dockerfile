FROM python:3.12-slim
WORKDIR /app
COPY mcp/requirements.txt .
COPY requirements-crawler.txt .
RUN pip install --no-cache-dir -r requirements.txt -r requirements-crawler.txt
COPY --chown=10001:10001 mcp/ ./
COPY --chown=10001:10001 lead_agent/ ./lead_agent/
COPY --chown=10001:10001 aibuds_crawler/ ./aibuds_crawler/
RUN useradd --uid 10001 --create-home appuser
USER appuser
CMD ["python", "server.py"]
