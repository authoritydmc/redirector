# Dockerfile for Flask URL Shortener/Redirector
FROM python:3.13-slim

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Fix CRLF line endings from Windows and make executable (critical for entrypoint)
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh && \
    sed -i 's/\r$//' gunicorn.conf.py || true

EXPOSE 80

ENTRYPOINT ["./entrypoint.sh"]
