# SERP service — API and worker share this image; the command selects which.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# lxml needs a compiler only if no wheel matches; keep the layer small either way.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libxml2 libxslt1.1 \
 && rm -rf /var/lib/apt/lists/*

COPY services/engine/requirements.txt ./engine-requirements.txt
COPY services/serp/requirements.txt ./serp-requirements.txt
RUN pip install -r engine-requirements.txt -r serp-requirements.txt

COPY shared/ ./shared/
COPY services/engine/seoagent/ ./seoagent/
COPY services/serp/ ./services/serp/
COPY services/__init__.py ./services/__init__.py

# The engine is imported as a top-level package, matching how the service does it.
ENV PYTHONPATH=/app

RUN useradd --system --uid 10004 seoagent \
 && mkdir -p /var/lib/seoagent/serp \
 && chown -R seoagent /var/lib/seoagent
USER seoagent

EXPOSE 8000
CMD ["uvicorn", "services.serp.api:app", "--host", "0.0.0.0", "--port", "8000"]
