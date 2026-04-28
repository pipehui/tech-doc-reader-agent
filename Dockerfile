FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1
ENV PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/

RUN pip install --upgrade pip && pip install -r requirements.txt

COPY tech_doc_agent /app/tech_doc_agent
COPY frontend /app/frontend
COPY graphs /app/graphs

EXPOSE 8000

CMD ["uvicorn", "tech_doc_agent.app.api.server:app", "--host", "0.0.0.0", "--port", "8000"]
