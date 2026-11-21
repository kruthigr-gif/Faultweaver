FROM python:3.11-slim

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY inventory_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY auth.py .
COPY inventory_service/app.py .

EXPOSE 80

USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
