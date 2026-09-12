.PHONY: setup smoke test-producer test-worker loadtest down validate
setup:
	./scripts/setup_localstack.sh
smoke:
	./scripts/smoke_test.sh
test-producer:
	cd services/producer && python3 -m pytest -q
test-worker:
	cd services/worker && go test -race ./...
loadtest:
	python3 loadtest/loadtest.py
down:
	docker compose down -v
validate:
	docker compose run --rm terraform init -backend=false
	docker compose run --rm terraform validate
