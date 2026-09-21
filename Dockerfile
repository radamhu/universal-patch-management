FROM python:3.12-alpine
ARG UPM_UID=1000
RUN apk add --no-cache openssh-client && adduser -D -u ${UPM_UID} upm
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ ./src/
COPY components.json .
ENV PYTHONPATH=/app/src PYTHONUNBUFFERED=1 DATA_DIR=/data COMPONENTS_PATH=/app/components.json
RUN mkdir /data && chown upm /data
USER upm
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"
CMD ["python", "-m", "upm.main"]
