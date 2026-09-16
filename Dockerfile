# Clep platform backend — hosted deploy.
# Playwright base image ships Chromium + deps; we add ffmpeg + Pillow only.
FROM mcr.microsoft.com/playwright/python:v1.63.0-jammy

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY platform/requirements.clep.txt .
RUN pip install --no-cache-dir -r requirements.clep.txt

COPY pipeline_clep/ pipeline_clep/
COPY platform/server.py platform/dashboard.html platform/

# State (registry.json, jobs.json, output MP4s) lives on a mounted volume.
RUN mkdir -p /data/output \
    && rm -rf pipeline_clep/output pipeline_clep/cache \
    && ln -s /data/output pipeline_clep/output \
    && ln -s /data/registry.json platform/registry.json \
    && ln -s /data/jobs.json platform/jobs.json

ENV HOST=0.0.0.0 PORT=8787
EXPOSE 8787

CMD ["python", "platform/server.py"]
