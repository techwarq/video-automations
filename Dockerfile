# Clep video service — hosted deploy (Cloud Run). Plans, records, and
# edits/renders clips; the API (auth, registry, job bookkeeping) is a
# separate Cloudflare Worker in platform/, not part of this image.
# Playwright base image ships Chromium + deps; we add ffmpeg + Pillow only.
FROM mcr.microsoft.com/playwright/python:v1.63.0-jammy

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pipeline_clep/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline_clep/ pipeline_clep/
RUN rm -rf pipeline_clep/output pipeline_clep/cache

ENV HOST=0.0.0.0 PORT=8788
EXPOSE 8788

CMD ["python", "pipeline_clep/service.py"]
