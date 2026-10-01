import json
import boto3
import pytest
from moto import mock_aws
from fastapi.testclient import TestClient
from botocore.exceptions import EndpointConnectionError
from app.main import create_app


@pytest.fixture
def api():
    with mock_aws():
        sqs = boto3.client("sqs", region_name="us-east-1")
        url = sqs.create_queue(QueueName="test")["QueueUrl"]
        with TestClient(create_app(sqs, url)) as client:
            yield client, sqs, url


def test_enqueue_and_stable_id(api):
    client, sqs, url = api
    a = client.post("/ingest", json={"device_id":"r1", "event_type":"up", "payload":{"b":2,"a":1}})
    b = client.post("/ingest", json={"payload":{"a":1,"b":2}, "event_type":"up", "device_id":"r1"})
    assert a.status_code == 202
    assert a.json()["event_id"] == b.json()["event_id"]
    messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)["Messages"]
    assert len(messages) == 2
    assert json.loads(messages[0]["Body"])["device_id"] == "r1"


def test_key_is_scoped_to_device(api):
    client, _, _ = api
    def send(device, kind):
        return client.post("/ingest", headers={"Idempotency-Key":"key"}, json={"device_id":device,"event_type":kind}).json()["event_id"]
    assert send("r1","up") == send("r1","down")
    assert send("r1","up") != send("r2","up")


def test_validation_and_health(api):
    client, _, _ = api
    assert client.post("/ingest", json={"device_id":""}).status_code == 422
    assert client.post("/ingest", json={"device_id":"a","event_type":"up","extra":1}).status_code == 422
    assert client.post("/ingest", json={"device_id":"a","event_type":"up","payload":{"x":"x"*200001}}).status_code == 413
    assert client.get("/healthz").status_code == 200


def test_queue_failure(api, monkeypatch):
    client, sqs, _ = api
    def fail(**kwargs):
        raise EndpointConnectionError(endpoint_url="http://unreachable")
    monkeypatch.setattr(sqs, "send_message", fail)
    assert client.post("/ingest", json={"device_id":"r1","event_type":"up"}).status_code == 503
    monkeypatch.setattr(sqs, "get_queue_attributes", fail)
    assert client.get("/healthz").status_code == 503


@pytest.mark.parametrize("key", [None, "stable-key"])
def test_received_at_is_utc_and_does_not_change_identity(api, monkeypatch, key):
    import hashlib
    from datetime import datetime, timezone
    import app.main as producer

    instants = iter([
        datetime(2026, 9, 30, 10, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 9, 30, 11, 0, 0, tzinfo=timezone.utc),
    ])
    class Clock:
        @staticmethod
        def now(tz):
            assert tz == timezone.utc
            return next(instants)
    monkeypatch.setattr(producer, "datetime", Clock)
    client, sqs, url = api
    event = {"device_id": "r1", "event_type": "up", "payload": {}}
    headers = {"Idempotency-Key": key} if key else {}
    responses = [client.post("/ingest", json=event, headers=headers) for _ in range(2)]
    assert all(res.status_code == 202 for res in responses)
    identity = json.dumps(["r1", key]) if key else json.dumps(event, sort_keys=True, separators=(",", ":"))
    expected_id = hashlib.sha256(identity.encode()).hexdigest()
    assert all(res.json()["event_id"] == expected_id for res in responses)
    messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10)["Messages"]
    bodies = [json.loads(msg["Body"]) for msg in messages]
    assert {body["received_at"] for body in bodies} == {"2026-09-30T10:00:00Z", "2026-09-30T11:00:00Z"}
    assert all(body["event_id"] == expected_id for body in bodies)


def test_client_cannot_supply_received_at(api):
    client, _, _ = api
    response = client.post("/ingest", json={"device_id": "r1", "event_type": "up", "received_at": "2020-01-01T00:00:00Z"})
    assert response.status_code == 422
