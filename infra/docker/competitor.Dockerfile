# Competitor service — API and worker share this image; the command selects which.
# Like the content and links services it never fetches the web: it reads crawls
# the crawl service has already made, yours and theirs alike.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/competitor/requirements.txt ./competitor-requirements.txt
RUN pip install -r competitor-requirements.txt

COPY shared/ ./shared/
COPY services/competitor/ ./services/competitor/
COPY services/__init__.py ./services/__init__.py

ENV PYTHONPATH=/app

RUN useradd --system --uid 10010 seoagent \
 && mkdir -p /var/lib/seoagent/competitor \
 && chown -R seoagent /var/lib/seoagent
USER seoagent

EXPOSE 8000
CMD ["uvicorn", "services.competitor.api:app", "--host", "0.0.0.0", "--port", "8000"]
