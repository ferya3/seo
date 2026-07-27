# Internal-links service — API and worker share this image; the command selects
# which. Lighter than the others: this service never fetches the web, it reads a
# crawl the crawl service already has, so no engine and no parser.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/internal_links/requirements.txt ./links-requirements.txt
RUN pip install -r links-requirements.txt

COPY shared/ ./shared/
COPY services/internal_links/ ./services/internal_links/
COPY services/__init__.py ./services/__init__.py

ENV PYTHONPATH=/app

RUN useradd --system --uid 10005 seoagent \
 && mkdir -p /var/lib/seoagent/links \
 && chown -R seoagent /var/lib/seoagent
USER seoagent

EXPOSE 8000
CMD ["uvicorn", "services.internal_links.api:app", "--host", "0.0.0.0", "--port", "8000"]
