"""RabbitMQ event bus: envelope construction, publishing, consuming.

Shared by every Python service. The contract it implements lives in
`shared/contracts/events/` — this module is the only place that knows how an
envelope becomes bytes on the wire, so a change there is a change in one place.

Topology (declared idempotently by both publisher and consumer, so neither has
to be started first):

    seo.events  (topic exchange, durable)
        └── bound by routing key, e.g. "crawl.*" or "page.updated"
            └── <queue>            durable, with a dead-letter binding
            └── <queue>.dlq        holds what the consumer rejected

Delivery is at-least-once. Consumers must be idempotent on `envelope.id`.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import pika
from pika.exceptions import AMQPError

log = logging.getLogger(__name__)

EXCHANGE = "seo.events"
DLX = "seo.events.dlx"
CONTRACT_VERSION = 1


def broker_url() -> str:
    return os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/%2F")


@dataclass
class Envelope:
    """One message on the bus. Mirrors contracts/events/envelope.json."""

    type: str
    payload: dict[str, Any]
    producer: str
    version: int = CONTRACT_VERSION
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    correlation_id: str | None = None
    causation_id: str | None = None
    tenant_id: str | None = None
    project_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "version": self.version,
            "occurred_at": self.occurred_at,
            "producer": self.producer,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "tenant_id": self.tenant_id,
            "project_id": self.project_id,
            "payload": self.payload,
        }

    @classmethod
    def from_bytes(cls, body: bytes) -> Envelope:
        raw = json.loads(body.decode("utf-8"))
        missing = {"id", "type", "version", "occurred_at", "producer", "payload"} - set(raw)
        if missing:
            raise ValueError(f"envelope missing required fields: {sorted(missing)}")
        return cls(
            id=raw["id"],
            type=raw["type"],
            version=raw["version"],
            occurred_at=raw["occurred_at"],
            producer=raw["producer"],
            payload=raw["payload"],
            correlation_id=raw.get("correlation_id"),
            causation_id=raw.get("causation_id"),
            tenant_id=raw.get("tenant_id"),
            project_id=raw.get("project_id"),
        )

    def caused(self, type: str, payload: dict[str, Any], producer: str) -> Envelope:
        """Build a follow-up event that keeps this one's trace.

        Using this instead of constructing an Envelope by hand is what makes a
        chain — crawl.completed → page.updated → optimizer — traceable end to
        end from a single correlation id.
        """
        return Envelope(
            type=type,
            payload=payload,
            producer=producer,
            correlation_id=self.correlation_id or self.id,
            causation_id=self.id,
            tenant_id=self.tenant_id,
            project_id=self.project_id,
        )


def _declare_topology(channel: pika.channel.Channel) -> None:
    channel.exchange_declare(EXCHANGE, exchange_type="topic", durable=True)
    channel.exchange_declare(DLX, exchange_type="topic", durable=True)


class Publisher:
    """Publishes envelopes. Reconnects on a dropped connection.

    Messages are persistent and published with publisher confirms, so a
    `publish()` that returns has been accepted by the broker — without confirms
    a broker restart silently eats in-flight messages.
    """

    def __init__(self, producer: str, url: str | None = None):
        self.producer = producer
        self.url = url or broker_url()
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.channel.Channel | None = None

    def _ensure_channel(self) -> pika.channel.Channel:
        if self._channel is not None and self._channel.is_open:
            return self._channel
        params = pika.URLParameters(self.url)
        params.client_properties = {"connection_name": f"{self.producer}@{socket.gethostname()}"}
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        _declare_topology(self._channel)
        self._channel.confirm_delivery()
        return self._channel

    def publish(self, envelope: Envelope) -> None:
        body = json.dumps(envelope.to_dict(), ensure_ascii=False).encode("utf-8")
        properties = pika.BasicProperties(
            content_type="application/json",
            delivery_mode=2,                      # persist across broker restart
            message_id=envelope.id,
            correlation_id=envelope.correlation_id,
            type=envelope.type,
            app_id=envelope.producer,
            timestamp=int(datetime.now(timezone.utc).timestamp()),
        )
        try:
            channel = self._ensure_channel()
            channel.basic_publish(EXCHANGE, envelope.type, body, properties)
        except AMQPError:
            # One retry on a fresh connection: a long-idle publisher usually
            # discovers the socket is dead only when it tries to use it.
            self.close()
            channel = self._ensure_channel()
            channel.basic_publish(EXCHANGE, envelope.type, body, properties)
        log.info("published %s id=%s", envelope.type, envelope.id)

    def emit(self, type: str, payload: dict[str, Any], **envelope_fields: Any) -> Envelope:
        envelope = Envelope(type=type, payload=payload, producer=self.producer, **envelope_fields)
        self.publish(envelope)
        return envelope

    def close(self) -> None:
        try:
            if self._connection is not None and self._connection.is_open:
                self._connection.close()
        except AMQPError:
            pass
        finally:
            self._connection = None
            self._channel = None

    def __enter__(self) -> Publisher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


Handler = Callable[[Envelope], None]


class Consumer:
    """Consumes envelopes for one queue.

    A handler that returns normally acks. A handler that raises sends the
    message to the dead-letter queue rather than requeuing it — an infinite
    redelivery loop on a poison message is worse than a message parked
    somewhere visible.
    """

    def __init__(
        self,
        queue: str,
        routing_keys: Iterable[str],
        handler: Handler,
        url: str | None = None,
        prefetch: int = 4,
    ):
        self.queue = queue
        self.routing_keys = list(routing_keys)
        self.handler = handler
        self.url = url or broker_url()
        self.prefetch = prefetch
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.channel.Channel | None = None

    @property
    def dlq(self) -> str:
        return f"{self.queue}.dlq"

    def declare(self, channel: pika.channel.Channel) -> None:
        _declare_topology(channel)

        channel.queue_declare(self.dlq, durable=True)
        channel.queue_bind(self.dlq, DLX, routing_key="#")

        channel.queue_declare(
            self.queue,
            durable=True,
            arguments={"x-dead-letter-exchange": DLX},
        )
        for key in self.routing_keys:
            channel.queue_bind(self.queue, EXCHANGE, routing_key=key)

    def _connect(self) -> pika.channel.Channel:
        params = pika.URLParameters(self.url)
        params.client_properties = {"connection_name": f"{self.queue}@{socket.gethostname()}"}
        self._connection = pika.BlockingConnection(params)
        self._channel = self._connection.channel()
        self.declare(self._channel)
        self._channel.basic_qos(prefetch_count=self.prefetch)
        return self._channel

    def _on_message(self, channel, method, properties, body) -> None:  # noqa: ANN001
        try:
            envelope = Envelope.from_bytes(body)
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.error("undecodable message on %s, dead-lettering: %s", self.queue, exc)
            channel.basic_nack(method.delivery_tag, requeue=False)
            return

        try:
            self.handler(envelope)
        except Exception:
            log.exception("handler failed for %s id=%s, dead-lettering", envelope.type, envelope.id)
            channel.basic_nack(method.delivery_tag, requeue=False)
            return

        channel.basic_ack(method.delivery_tag)

    def run(self) -> None:
        """Block, consuming until interrupted."""
        channel = self._connect()
        channel.basic_consume(self.queue, self._on_message)
        log.info("consuming %s bound to %s", self.queue, ", ".join(self.routing_keys))
        try:
            channel.start_consuming()
        except KeyboardInterrupt:
            channel.stop_consuming()
        finally:
            self.close()

    def close(self) -> None:
        try:
            if self._connection is not None and self._connection.is_open:
                self._connection.close()
        except AMQPError:
            pass
        finally:
            self._connection = None
            self._channel = None
