# Reporting service — API and worker share this image; the command selects
# which. Renders a finished workflow as a document; no crawler, no model, no
# PDF engine — printing the HTML is what makes a PDF.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/reporting/requirements.txt ./reporting-requirements.txt
RUN pip install -r reporting-requirements.txt

COPY shared/ ./shared/
COPY services/reporting/ ./services/reporting/
COPY services/__init__.py ./services/__init__.py

ENV PYTHONPATH=/app

RUN useradd --system --uid 10008 seoagent \
 && mkdir -p /var/lib/seoagent/reports \
 && chown -R seoagent /var/lib/seoagent
USER seoagent

EXPOSE 8000
CMD ["uvicorn", "services.reporting.api:app", "--host", "0.0.0.0", "--port", "8000"]
