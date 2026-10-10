import hashlib
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from .events import EventPage, list_events


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=128)
    payload: dict = Field(default_factory=dict)


def create_app(sqs=None, queue_url=None, dynamodb=None, table_name=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.sqs = sqs if sqs is not None else boto3.client(
            "sqs", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
            config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 2}),
        )
        app.state.queue_url = queue_url or app.state.sqs.get_queue_url(
            QueueName=os.getenv("QUEUE_NAME", "telemetry-events"))["QueueUrl"]
        app.state.dynamodb = dynamodb if dynamodb is not None else boto3.client(
            "dynamodb", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            endpoint_url=os.getenv("AWS_ENDPOINT_URL") or None,
            config=Config(connect_timeout=2, read_timeout=3, retries={"max_attempts": 2}),
        )
        app.state.table_name = table_name or os.getenv("TABLE_NAME", "telemetry-events")
        yield

    app = FastAPI(title="Telemetry ingestion", lifespan=lifespan)

    @app.get("/healthz")
    def health():
        try:
            app.state.sqs.get_queue_attributes(QueueUrl=app.state.queue_url, AttributeNames=["QueueArn"])
        except (BotoCoreError, ClientError):
            raise HTTPException(503, "Queue unavailable")
        return {"status": "ok"}

    @app.get("/events", response_model=EventPage, summary="Browse stored telemetry reports")
    def events(
        limit: int = Query(default=25, ge=1, le=100, description="Maximum reports per page"),
        cursor: str | None = Query(default=None, min_length=1, max_length=12000, description="next_cursor from the previous response"),
    ):
        """Unordered, eventually consistent scan. Continue until next_cursor is null."""
        return list_events(app.state.dynamodb, app.state.table_name, limit, cursor)

    @app.post("/ingest", status_code=202)
    def ingest(event: Telemetry, idempotency_key: str | None = Header(default=None, min_length=1, max_length=256)):
        received_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        try:
            canonical = json.dumps(event.model_dump(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (ValueError, TypeError):
            raise HTTPException(422, "Payload must contain valid JSON values")
        # A supplied key is scoped by device. First accepted payload wins for a reused key.
        identity = json.dumps([event.device_id, idempotency_key]) if idempotency_key is not None else canonical
        event_id = hashlib.sha256(identity.encode()).hexdigest()
        body = json.dumps({**event.model_dump(), "event_id": event_id, "received_at": received_at}, allow_nan=False)
        if len(body.encode()) > 200_000:
            raise HTTPException(413, "Event exceeds 200 KB")
        try:
            app.state.sqs.send_message(QueueUrl=app.state.queue_url, MessageBody=body)
        except (BotoCoreError, ClientError):
            raise HTTPException(503, "Queue unavailable; retry with the same idempotency key")
        return {"event_id": event_id, "status": "queued"}

    return app


app = create_app()
