# Orchestrator — API and worker share this image; the command selects which.
#
# No SEO engine in it: the orchestrator never crawls or researches, it only
# decides what should happen next and puts that on the bus.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY agents/orchestrator/requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY shared/ ./shared/
COPY agents/__init__.py ./agents/__init__.py
COPY agents/orchestrator/ ./agents/orchestrator/

ENV PYTHONPATH=/app

RUN useradd --system --uid 10003 orchestrator
USER orchestrator

EXPOSE 8000
CMD ["uvicorn", "agents.orchestrator.api:app", "--host", "0.0.0.0", "--port", "8000"]
