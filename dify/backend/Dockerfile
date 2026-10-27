FROM python:3.10-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend /app/backend

EXPOSE 8765
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8765"]
