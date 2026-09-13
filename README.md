# Distributed Telemetry Processing Platform

A cloud-native backend for reliable, asynchronous processing of simulated network telemetry.

**Technology stack:** Python · FastAPI · Go · Amazon SQS · DynamoDB · API Gateway · Terraform · Docker · LocalStack · GitHub Actions

**Status:** Core backend implemented and verified locally. A web dashboard and production deployment are planned extensions.

## Overview

This platform receives device events, queues them for asynchronous processing, and stores them with duplicate protection.

It models a common infrastructure challenge: accepting incoming reports while downstream processing may be slower, temporarily unavailable, or subject to repeated delivery.

A Python API validates incoming reports, Go workers process queued events concurrently, and DynamoDB conditional writes enforce idempotent storage. Terraform defines the infrastructure, while Docker and LocalStack provide a repeatable local development environment.

The current implementation uses simulated telemetry. Direct integration with physical routers is planned future work.

## Problem Statement

Network devices and monitoring systems can generate bursts of operational events. Processing every report immediately within the receiving API can make ingestion sensitive to database delays and failures.

This project separates ingestion from processing and addresses several reliability concerns:

| Challenge | Implemented solution |
| --- | --- |
| Incoming events outpace processing | SQS buffers accepted messages while workers process the backlog. |
| Processing fails temporarily | Unacknowledged messages become available for automatic retry. |
| Events arrive more than once | Stable event IDs and conditional writes prevent duplicate records. |
| A message repeatedly fails | A redrive policy moves it to a dead-letter queue. |
| Dependencies become unavailable | Health checks report dependency failures. |
| Environments require consistent infrastructure | Terraform and Docker Compose define the local setup. |

## Architecture

```text
Simulated Client
      |
      | POST /ingest
      v
API Gateway
      |
      v
FastAPI Producer
  Validate event
  Derive event ID
      |
      v
Amazon SQS --------------------------> Dead-Letter Queue
      |                                After retry limit
      | Long polling
      v
Concurrent Go Workers
      |
      | Conditional write
      v
DynamoDB
```

Local clients can also access the producer directly.

An **HTTP 202 Accepted** response confirms that SQS accepted the report. Database processing completes asynchronously.

## Implemented Features

### Event Ingestion

- FastAPI endpoint for receiving telemetry.
- Validation of device identifiers, event types, and JSON payloads.
- Rejection of unknown top-level fields and oversized serialized events.
- Deterministic event IDs derived from a device-scoped idempotency key or canonical payload hash.
- Explicit error responses when the queue is unavailable.

### Concurrent Processing

- Go workers with configurable concurrency.
- SQS long polling.
- Processing deadlines shorter than the visibility timeout.
- Message acknowledgement after successful storage or detection of an existing record.
- Cancellation of polling and processing during shutdown.

### Reliability and Duplicate Protection

- DynamoDB conditional writes using `attribute_not_exists(event_id)`.
- Automatic redelivery of unacknowledged messages.
- Dead-letter routing after the configured receive limit.
- Dependency-aware health checks for the producer and worker.

The system uses **at-least-once delivery with idempotent storage**, rather than exactly-once message delivery.

Reusing an idempotency key with a different payload retains whichever event is stored first.

### Infrastructure and Developer Tooling

- Terraform modules for SQS, DynamoDB, and API Gateway.
- Dockerfiles configured for non-root application execution.
- Docker Compose setup with LocalStack.
- Setup and end-to-end smoke-test scripts.
- HTTP load generator with latency, throughput, and error-rate reporting.
- GitHub Actions workflow defining tests, infrastructure validation, container builds, and integration checks.
- Optional ECS Fargate worker module, not yet verified in AWS.


## Engineering Skills Demonstrated

- **Backend development:** API validation, HTTP response semantics, and dependency failure handling.
- **Concurrent programming:** Go goroutines, bounded processing, cancellation, and race testing.
- **Distributed systems:** asynchronous processing, at-least-once delivery, idempotency, and dead-letter handling.
- **Infrastructure as code:** modular Terraform configuration and environment-specific settings.
- **Containerization:** service packaging, networking, non-root execution, and health checks.
- **Testing and debugging:** unit tests, SDK-level failure tests, integration testing, and diagnosis of container-specific issues.

## Repository Structure

```text
.
├── .github/workflows/ci.yml
├── .gitignore
├── README.md
└── distributed-telemetry-platform/
    ├── services/
    │   ├── producer/       # FastAPI service, tests, and Dockerfile
    │   └── worker/         # Go consumer, tests, and Dockerfile
    ├── terraform/
    │   ├── modules/
    │   │   ├── api_gateway/
    │   │   ├── sqs/
    │   │   ├── dynamodb/
    │   │   └── ecs/
    │   └── envs/
    ├── scripts/            # Setup and smoke-test helpers
    ├── loadtest/           # HTTP load generator
    ├── compose.yaml
    ├── Makefile
    ├── README.md
    └── VERIFICATION.md
```

## Run Locally

### Prerequisites

- Docker with Docker Compose.
- A running Docker engine.

### Start the Platform

From the repository root:

```sh
cd distributed-telemetry-platform
make setup
```

### Run the End-to-End Test

```sh
make smoke
```

The smoke test can take several minutes because it exercises retry timeouts and dead-letter delivery.

### Submit a Sample Event

Open the interactive API documentation:

http://localhost:8000/docs

Or send a request directly:

```sh
curl -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: example-1" \
  -d '{
    "device_id": "router-42",
    "event_type": "link_flap",
    "payload": {
      "iface": "eth0"
    }
  }'
```

### Additional Commands

```sh
make loadtest   # Generate an HTTP acceptance performance report
make down       # Stop the local stack
```

LocalStack data is ephemeral in the current configuration.

## Development Roadmap

The following capabilities are planned, not completed features or scheduled commitments.

### Phase 1: Web Dashboard

- Form for submitting simulated device reports.
- Stored-event list and detail pages.
- Service-health overview.
- Basic device and event-type filtering.
- Paginated backend read endpoints.
- Clear distinction between accepted and stored reports.

### Phase 2: Operational Visibility

- Approximate queue-depth and dead-letter counts.
- Failed-message investigation views.
- Additional metrics and processing diagnostics.
- Controlled replay of failed messages.

Precise per-event processing history will require additional status tracking.

### Phase 3: Secure Hosting

- Authentication and authorization.
- Protected ingestion and operational endpoints.
- HTTPS and secret management.
- Producer and dashboard deployment.
- Validation of AWS networking and the optional ECS worker.
- Deployment checks and monitoring.

### Phase 4: Real-Device Integration

- Identify supported router reporting methods.
- Configure or build a device-data collector.
- Normalize device reports into the platform's event format.
- Secure the connection and test with physical equipment.

## Current Limitations

- Telemetry is simulated.
- The system has been verified locally, not in production.
- A custom dashboard and authentication are not implemented.
- Physical routers are not connected.
- Outage alerts, trend analysis, and automatic device repair are not included.
- Large-scale capacity and the optional AWS deployment remain unverified.

## Portfolio Summary

Developed and locally validated a distributed telemetry backend using Python, Go, SQS, DynamoDB, Terraform, and Docker. Implemented asynchronous ingestion, concurrent processing, idempotent storage, automatic retries, and dead-letter handling. Verified the local pipeline through unit tests, race detection, container builds, infrastructure provisioning, and end-to-end testing.

Planned extensions include a web dashboard, secure hosting, and real-device integration.
