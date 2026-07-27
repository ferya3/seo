# Content service — API and worker share this image; the command selects which.
# Like the links service it never fetches the web: it reads a crawl and a
# keyword study the other services have already produced.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/content/requirements.txt ./content-requirements.txt
RUN pip install -r content-requirements.txt

COPY shared/ ./shared/
COPY services/content/ ./services/content/
COPY services/__init__.py ./services/__init__.py

ENV PYTHONPATH=/app

RUN useradd --system --uid 10006 seoagent \
 && mkdir -p /var/lib/seoagent/content \
 && chown -R seoagent /var/lib/seoagent
USER seoagent

EXPOSE 8000
CMD ["uvicorn", "services.content.api:app", "--host", "0.0.0.0", "--port", "8000"]
