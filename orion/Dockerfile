FROM python:3.11-slim

WORKDIR /app

COPY orion/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY orion/ ./orion/

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin orion
USER orion

EXPOSE 8000

CMD ["uvicorn", "orion.app:app", "--host", "0.0.0.0", "--port", "8000"]
