# Clear Sky — single-process Python service, no build step, no framework.
FROM python:3.12-slim

WORKDIR /srv

# Only optional dependency: cryptography, for Web Push (VAPID / RFC 8291).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

# The database lives on the mounted volume, never in the image.
ENV PORT=8080 \
    DB_PATH=/data/alerts.sqlite \
    PYTHONUNBUFFERED=1
RUN mkdir -p /data

EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8080')+'/api/version',timeout=4)"

CMD ["python", "app/server.py"]
