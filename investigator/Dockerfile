FROM python:3.11-slim

WORKDIR /app

RUN useradd --create-home --uid 10001 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 80

USER appuser

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
