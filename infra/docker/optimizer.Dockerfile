# Optimizer service — API and worker share this image; the command selects
# which. The only service image that can talk to Claude, so `anthropic` is
# installed here and nowhere else. Without a key it simply is not used.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY services/optimizer/requirements.txt ./optimizer-requirements.txt
RUN pip install -r optimizer-requirements.txt anthropic

COPY shared/ ./shared/
COPY services/optimizer/ ./services/optimizer/
COPY services/__init__.py ./services/__init__.py

ENV PYTHONPATH=/app

RUN useradd --system --uid 10007 seoagent \
 && mkdir -p /var/lib/seoagent/optimizer \
 && chown -R seoagent /var/lib/seoagent
USER seoagent

EXPOSE 8000
CMD ["uvicorn", "services.optimizer.api:app", "--host", "0.0.0.0", "--port", "8000"]
