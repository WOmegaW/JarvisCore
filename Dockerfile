FROM python:3.13-slim
RUN apt-get update && apt-get install -y --no-install-recommends libzbar0 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt pyzbar==0.1.9 Pillow==12.3.0
COPY bot ./bot
CMD ["python", "-m", "bot.main"]
