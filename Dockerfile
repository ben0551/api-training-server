FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# the SQLite database lives here — bind-mount it so it survives a rebuild
ENV DATA_DIR=/data \
    PORT=8080 \
    RATE_LIMIT=5000 \
    RATE_WINDOW_MINUTES=15
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=4s --start-period=8s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3).status==200 else 1)"

# --proxy-headers + forwarded-allow-ips so the console builds correct https share URLs
# and marks its session cookie Secure behind Traefik.
CMD ["python3","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8080", \
     "--proxy-headers","--forwarded-allow-ips","*"]
