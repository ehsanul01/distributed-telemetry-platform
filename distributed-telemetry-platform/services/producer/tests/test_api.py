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
