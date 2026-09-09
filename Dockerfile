FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME ["/app/data"]

EXPOSE 8098

CMD ["python", "app.py", "--config", "/app/data/config.json", "--db", "/app/data/bridge.db"]
