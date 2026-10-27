# Backend image for Azure Container Registry / App Service.
# Build context is the backend/ folder:  docker build -t aria-backend backend
# (or build in Azure without local Docker:  az acr build -r <acr> -t aria-backend:latest backend)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt requirements-azure.txt ./
RUN pip install --no-cache-dir -r requirements-azure.txt

COPY app ./app

# App Service passes the port via WEBSITES_PORT; default 8000 for local runs.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
