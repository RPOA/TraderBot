FROM python:3.12-slim-bookworm

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        chromium-driver \
        xvfb \
        x11vnc \
        fluxbox \
        fonts-liberation \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src /app/src
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app
ENV DISPLAY=:99
ENV CHROME_BIN=/usr/bin/chromium
ENV CHROME_DRIVER_PATH=/usr/bin/chromedriver

EXPOSE 8090 5900

CMD ["/app/start.sh"]
