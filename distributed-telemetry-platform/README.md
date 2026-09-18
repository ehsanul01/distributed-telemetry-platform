# Distributed Telemetry Processing Platform

Receives network telemetry through a Python API, queues it in SQS, and asynchronously stores it with a Go worker in DynamoDB. Terraform provisions the resources. The default environment uses the pinned LocalStack 4.4.0 image and requires Docker with Compose; no AWS credentials are needed for that local configuration.

## Run locally

From this directory:

```sh
make setup
make smoke
```

Setup starts LocalStack, applies Terraform, builds both services and waits for health checks. Smoke testing checks ingestion, stable IDs, persistence, and malformed-message delivery to the dead-letter queue. It can take about three minutes because it exercises real visibility timeouts.

- API documentation: http://localhost:8000/docs
- Producer health: http://localhost:8000/healthz
- Worker health: http://localhost:8080/healthz

```sh
curl http://localhost:8000/ingest -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: example-1' \
  -d '{"device_id":"router-42","event_type":"link_flap","payload":{"iface":"eth0"}}'
```

The API returns `202` and an `event_id` once SQS accepts the message; this does not mean database processing has completed.

Terraform also creates an API Gateway with `POST /v1/ingest`. To exercise that route inside the Docker network, obtain `api_id` with `docker compose run --rm terraform output -raw api_id`, then send a request to `http://localstack:4566/restapis/API_ID/v1/_user_request_/ingest`. Direct producer access is the default for local tests.

Stop with `make down`. LocalStack data is ephemeral: setup reapplies Terraform on the next run to recreate missing resources. Terraform state stays in `terraform/` and is ignored by Git. This Compose setup does not mount the Docker socket or enable LocalStack persistence.

## Folder and code guide

| Path | Responsibility |
| --- | --- |
| `services/producer/app/main.py` | FastAPI model validation, deterministic IDs, SQS enqueue and queue health |
| `services/producer/tests/test_api.py` | API tests with mocked SQS |
| `services/producer/Dockerfile` | Builds the Python service |
| `services/worker/main.go` | Concurrent long polling, database writes, acknowledgements, health and shutdown |
| `services/worker/main_test.go`, `store_test.go` | Processing failure, malformed-message, duplicate and acknowledgement tests |
| `services/worker/Dockerfile` | Builds a small non-root Go container |
| `terraform/main.tf` | Connects resource modules and validates deployment inputs |
| `terraform/providers.tf` | Selects AWS credentials/endpoints or LocalStack |
| `terraform/variables.tf`, `outputs.tf` | Configuration inputs and resource addresses |
| `terraform/modules/sqs/` | Queue, DLQ and redrive policies |
| `terraform/modules/dynamodb/` | On-demand table keyed by event ID |
| `terraform/modules/api_gateway/` | POST route and HTTP proxy integration |
| `terraform/modules/ecs/` | Optional Fargate worker in an existing VPC |
| `terraform/envs/` | Local and AWS configuration examples |
| `compose.yaml` | LocalStack, Terraform runner, producer and worker |
| `scripts/` | Setup and end-to-end checks |
| `loadtest/loadtest.py` | Concurrent HTTP load generator and Markdown report |
| `Makefile` | Shortcuts for running and testing |
| `../.github/workflows/ci.yml` | Unit, race, Terraform, container-build and integration checks |

## Processing guarantees

The producer validates required fields, rejects unknown top-level fields, and caps serialized events at 200 KB. A supplied `Idempotency-Key` is hashed with the device ID; otherwise the canonical payload is hashed. Identical payloads without a key intentionally collapse onto one stored event. Use distinct keys for separate occurrences with identical payloads. Reusing a key with a changed payload preserves whichever event is processed first; it does not report a conflict.

Each worker polls one message at a time; `WORKER_CONCURRENCY` defaults to four and accepts 1–64. Processing has a ten-second deadline, shorter than the 30-second visibility timeout. DynamoDB uses `attribute_not_exists(event_id)`. A conditional-write collision is treated as success and acknowledged. Other processing or delete failures leave the message for retry. After five receives, the queue redrive policy sends it to the DLQ. This provides at-least-once delivery and idempotent storage, not exactly-once message delivery.

The worker checks SQS and DynamoDB every five seconds. The producer checks SQS when `/healthz` is requested. Dependency failures return 503. SIGTERM cancels worker polling and processing; unfinished messages remain available for redelivery.

## Tests and load testing

For host-side tests, install Python 3.12+ and Go 1.24+:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r services/producer/requirements-dev.txt
make test-producer test-worker
make validate
make loadtest
```

The load report is written to `loadtest/report.md`. It measures HTTP acceptance latency and request throughput, not completed database writes. Failed requests cause a nonzero exit. Options include `--requests`, `--concurrency`, `--url`, and `--output`.




## Optional real AWS infrastructure

No cloud resources are deployed automatically. `terraform/envs/aws.tfvars` is deliberately an incomplete example. Set a real publicly reachable HTTPS `producer_endpoint` before applying it. This module uses a public HTTP proxy; an internal load balancer would require a separate VPC Link integration. The producer must be deployed separately with SQS permissions.

For the optional worker, set `deploy_ecs=true`, `worker_image` to a reachable Linux amd64 image, and supply existing private subnet and security group IDs. Those subnets need NAT or the necessary AWS VPC endpoints for SQS, DynamoDB, image pulls and logs. Use an existing ECR repository or other supported image registry. The module supplies scoped worker IAM permissions, execution role, logs, task and service. It does not create a VPC, ECR repository, producer service, public authentication or a production deployment pipeline.

```sh
cd terraform
terraform init
terraform plan -var-file=envs/aws.tfvars -var='producer_endpoint=https://YOUR_HOST'
# Review the plan before applying it. AWS usage incurs costs.
```

CI tests and builds images but does not publish them or deploy to AWS. Add authentication, rate controls and a producer deployment appropriate to your environment before exposing production ingestion.

References: [LocalStack Terraform configuration](https://docs.localstack.cloud/aws/connecting/infrastructure-as-code/terraform/) and [AWS provider HTTP proxy integration](https://registry.terraform.io/providers/hashicorp/aws/latest/docs/resources/api_gateway_integration).

See [VERIFICATION.md](VERIFICATION.md) for checks actually performed and remaining environment-dependent checks.

Local queue URLs use `SQS_ENDPOINT_STRATEGY="off"` so Terraform receives `/account-id/queue-name` paths. The `path` strategy adds `/queue/region/`, which the pinned AWS provider rejects. This local setup uses only `us-east-1`.
