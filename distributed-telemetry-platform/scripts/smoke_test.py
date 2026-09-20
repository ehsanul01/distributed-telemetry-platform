import json
import os
import time
import uuid
import urllib.request
import boto3

base = "http://producer:8000"
key = str(uuid.uuid4())
data = json.dumps({"device_id": "smoke-"+key, "event_type": "link_flap", "payload": {"iface": "eth0"}}).encode()
def send():
    req = urllib.request.Request(base+"/ingest", data=data, headers={"Content-Type":"application/json", "Idempotency-Key":key})
    with urllib.request.urlopen(req, timeout=10) as res:
        assert res.status == 202
        return json.load(res)["event_id"]
event_id = send()
assert send() == event_id
client = boto3.client("dynamodb", endpoint_url=os.environ["AWS_ENDPOINT_URL"])
for _ in range(60):
    result = client.get_item(TableName="telemetry-events", Key={"event_id":{"S":event_id}}, ConsistentRead=True)
    if "Item" in result:
        assert result["Item"]["device_id"]["S"] == "smoke-"+key
        break
    time.sleep(1)
else:
    raise RuntimeError("Event did not reach DynamoDB within 60 seconds")
print("PASS: ingestion, stable event ID and persistence. Checking retries and DLQ (up to 210 seconds)...", flush=True)
# A malformed message must survive failures and eventually reach the DLQ.
sqs = boto3.client("sqs", endpoint_url=os.environ["AWS_ENDPOINT_URL"])
url = sqs.get_queue_url(QueueName="telemetry-events")["QueueUrl"]
dlq = sqs.get_queue_url(QueueName="telemetry-events-dlq")["QueueUrl"]
poison = "malformed-smoke-"+key
sqs.send_message(QueueUrl=url, MessageBody=poison)
deadline = time.monotonic()+210
while time.monotonic()<deadline:
    for msg in sqs.receive_message(QueueUrl=dlq, WaitTimeSeconds=5, MaxNumberOfMessages=10).get("Messages",[]):
        if msg["Body"] == poison:
            sqs.delete_message(QueueUrl=dlq, ReceiptHandle=msg["ReceiptHandle"])
            print("PASS: ingestion, stable event ID, persistence, retries and DLQ")
            raise SystemExit(0)
        sqs.change_message_visibility(QueueUrl=dlq, ReceiptHandle=msg["ReceiptHandle"], VisibilityTimeout=0)
raise RuntimeError("Malformed event did not reach DLQ")
