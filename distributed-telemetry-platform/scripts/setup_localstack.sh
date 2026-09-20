#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
docker compose up -d --wait localstack
docker compose run --rm terraform init -input=false
docker compose run --rm terraform apply -input=false -auto-approve -var-file=envs/localstack.tfvars
docker compose up -d --build --wait producer worker
printf 'Platform ready: http://localhost:8000/docs\n'
