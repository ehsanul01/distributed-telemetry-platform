import base64
import json

import boto3
import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient
from moto import mock_aws

from app.main import create_app


@pytest.fixture
def reports():
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="events-test")["QueueUrl"]
        db = boto3.client("dynamodb", region_name="us-east-1")
        table = "custom-events"
        db.create_table(TableName=table, BillingMode="PAY_PER_REQUEST",
                        KeySchema=[{"AttributeName": "event_id", "KeyType": "HASH"}],
                        AttributeDefinitions=[{"AttributeName": "event_id", "AttributeType": "S"}])
        with TestClient(create_app(sqs, url, db, table)) as client:
            yield client, db, table


def put(db, table, event_id, timestamps=False):
    values = {"event_id": event_id, "device_id": "router-42", "event_type": "link_flap",
              "payload": json.dumps({"iface": "eth0", "count": 2, "nested": {"up": False}})}
    if timestamps:
        values.update(received_at="2026-09-30T10:00:00Z", processed_at="2026-09-30T10:00:01Z")
    db.put_item(TableName=table, Item={k: {"S": v} for k, v in values.items()})


def test_empty_table(reports):
    client, _, _ = reports
    response = client.get("/events")
    assert response.status_code == 200
    assert response.json() == {"events": [], "next_cursor": None}


def test_pages_preserve_data_and_legacy_records(reports):
    client, db, table = reports
    for index in range(5):
        put(db, table, f"event-{index}", timestamps=index != 0)
    found = []
    params = {"limit": 2}
    for _ in range(5):
        response = client.get("/events", params=params)
        assert response.status_code == 200
        page = response.json()
        assert len(page["events"]) <= 2
        found.extend(page["events"])
        if page["next_cursor"] is None:
            break
        params["cursor"] = page["next_cursor"]
    else:
        pytest.fail("Pagination did not terminate")
    assert len(found) == 5
    assert {row["event_id"] for row in found} == {f"event-{i}" for i in range(5)}
    for row in found:
        assert row["payload"] == {"iface": "eth0", "count": 2, "nested": {"up": False}}
        assert row["device_id"] == "router-42"
        assert row["event_type"] == "link_flap"
        assert row["received_at"] == (None if row["event_id"] == "event-0" else "2026-09-30T10:00:00Z")
        assert row["processed_at"] == (None if row["event_id"] == "event-0" else "2026-09-30T10:00:01Z")


@pytest.mark.parametrize("limit", [0, -1, 101, "abc", "1.5"])
def test_invalid_limit(reports, limit):
    assert reports[0].get("/events", params={"limit": limit}).status_code == 422


@pytest.mark.parametrize("raw", ["not-json", "[]", '{}', '{"version":2,"event_id":"x"}',
    '{"version":true,"event_id":"x"}', '{"version":1,"event_id":123}',
    '{"version":1,"event_id":""}', '{"version":1,"event_id":"x","extra":"y"}'])
def test_invalid_cursor_content(reports, raw):
    cursor = base64.urlsafe_b64encode(raw.encode()).decode()
    assert reports[0].get("/events", params={"cursor": cursor}).status_code == 400


@pytest.mark.parametrize("cursor,status", [("!!", 400), ("", 422), ("x" * 12001, 422)])
def test_invalid_cursor_encoding_or_length(reports, cursor, status):
    assert reports[0].get("/events", params={"cursor": cursor}).status_code == status


@pytest.mark.parametrize("error", [EndpointConnectionError(endpoint_url="http://db"),
    ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "private-detail"}}, "Scan"),
    ClientError({"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "private-detail"}}, "Scan")])
def test_database_failures(reports, monkeypatch, error):
    client, db, _ = reports
    def fail(**kwargs):
        raise error
    monkeypatch.setattr(db, "scan", fail)
    response = client.get("/events")
    assert response.status_code == 503
    assert response.json() == {"detail": "Database unavailable; retry later"}


def test_empty_page_with_continuation_and_default_limit(reports, monkeypatch):
    client, db, table = reports
    calls = []
    def scan(**kwargs):
        calls.append(kwargs)
        return {"Items": [], "LastEvaluatedKey": {"event_id": {"S": "last-event"}}} if len(calls) == 1 else {"Items": []}
    monkeypatch.setattr(db, "scan", scan)
    first = client.get("/events").json()
    assert first["events"] == [] and first["next_cursor"]
    assert client.get("/events", params={"cursor": first["next_cursor"]}).json()["next_cursor"] is None
    assert calls == [{"TableName": table, "Limit": 25},
                     {"TableName": table, "Limit": 25, "ExclusiveStartKey": {"event_id": {"S": "last-event"}}}]


def test_corrupt_payload_has_clear_error(reports):
    client, db, table = reports
    put(db, table, "broken")
    db.update_item(TableName=table, Key={"event_id": {"S": "broken"}},
                   UpdateExpression="SET payload = :p", ExpressionAttributeValues={":p": {"S": "not-json"}})
    response = client.get("/events")
    assert response.status_code == 500
    assert response.json() == {"detail": "Stored event data is invalid"}


def test_openapi_describes_pagination_and_response(reports):
    schema = reports[0].get("/openapi.json").json()
    operation = schema["paths"]["/events"]["get"]
    assert {p["name"] for p in operation["parameters"]} == {"limit", "cursor"}
    assert "EventPage" in schema["components"]["schemas"]
