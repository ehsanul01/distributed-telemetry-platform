"""Bounded event browsing for the local dashboard API."""
import base64
import binascii
import json

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import HTTPException
from pydantic import BaseModel


class StoredEvent(BaseModel):
    event_id: str
    device_id: str
    event_type: str
    payload: dict
    received_at: str | None = None
    processed_at: str | None = None


class EventPage(BaseModel):
    events: list[StoredEvent]
    next_cursor: str | None = None


def encode_cursor(key):
    data = json.dumps({"version": 1, "event_id": key["event_id"]["S"]}, separators=(",", ":"))
    return base64.urlsafe_b64encode(data.encode()).decode()


def decode_cursor(cursor):
    try:
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(data, dict) or set(data) != {"version", "event_id"}:
            raise ValueError
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError
        if not isinstance(data["event_id"], str) or not 1 <= len(data["event_id"].encode()) <= 2048:
            raise ValueError
        return {"event_id": {"S": data["event_id"]}}
    except (ValueError, TypeError, UnicodeError, binascii.Error):
        raise HTTPException(400, "Invalid pagination cursor") from None


def list_events(client, table_name, limit, cursor):
    request = {"TableName": table_name, "Limit": limit}
    if cursor is not None:
        request["ExclusiveStartKey"] = decode_cursor(cursor)
    try:
        page = client.scan(**request)
    except (BotoCoreError, ClientError):
        raise HTTPException(503, "Database unavailable; retry later") from None
    try:
        events = []
        for item in page.get("Items", []):
            # The Go worker stores payload as a JSON string, not a DynamoDB map.
            values = {name: item[name]["S"] for name in ("event_id", "device_id", "event_type")}
            values["payload"] = json.loads(item["payload"]["S"])
            for name in ("received_at", "processed_at"):
                values[name] = item[name]["S"] if name in item else None
            events.append(StoredEvent(**values))
        key = page.get("LastEvaluatedKey")
        return EventPage(events=events, next_cursor=encode_cursor(key) if key else None)
    except (KeyError, TypeError, ValueError):
        raise HTTPException(500, "Stored event data is invalid") from None
