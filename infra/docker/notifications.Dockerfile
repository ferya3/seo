# Notifications service — API and worker share this image; the command selects
# which. The only service that makes requests to addresses a user typed, which
# is why the SSRF guard travels with it.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/notifications/requirements.txt ./notifications-requirements.txt
RUN pip install -r notifications-requirements.txt

COPY shared/ ./shared/
COPY services/notifications/ ./services/notifications/
COPY services/__init__.py ./services/__init__.py

ENV PYTHONPATH=/app

RUN useradd --system --uid 10009 seoagent \
 && mkdir -p /var/lib/seoagent/notifications \
 && chown -R seoagent /var/lib/seoagent
USER seoagent

EXPOSE 8000
CMD ["uvicorn", "services.notifications.api:app", "--host", "0.0.0.0", "--port", "8000"]
