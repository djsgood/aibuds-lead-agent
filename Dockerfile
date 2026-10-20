FROM python:3.12-slim
WORKDIR /app
COPY mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp/ ./
RUN useradd --uid 10001 --create-home appuser
USER appuser
CMD ["python", "server.py"]
