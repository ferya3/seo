# Outbox relay — drains staged events from Postgres onto RabbitMQ.
#
# Deliberately its own image with no service code in it: the relay must not be
# able to import a service, because the moment it can, someone will make it do
# work that belongs in a consumer.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY infra/docker/relay-requirements.txt ./requirements.txt
RUN pip install -r requirements.txt

COPY shared/ ./shared/

ENV PYTHONPATH=/app

RUN useradd --system --uid 10002 relay
USER relay

CMD ["python", "-m", "shared.relay"]
