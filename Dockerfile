FROM python:3.12-slim
WORKDIR /app
COPY requirements-pipeline.txt ./
RUN pip install --no-cache-dir -r requirements-pipeline.txt
COPY lead_agent/ ./lead_agent/
COPY config/ ./config/
CMD ["python", "-m", "lead_agent", "metrics"]
