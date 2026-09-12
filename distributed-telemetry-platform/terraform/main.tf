module "sqs" {
  source            = "./modules/sqs"
  name              = var.name
  max_receive_count = var.max_receive_count
}
module "dynamodb" {
  source = "./modules/dynamodb"
  name   = var.name
}
module "api_gateway" {
  source            = "./modules/api_gateway"
  name              = var.name
  producer_endpoint = var.producer_endpoint
}

resource "terraform_data" "configuration" {
  lifecycle {
    precondition {
      condition     = !var.deploy_ecs || (!var.use_localstack && var.worker_image != "" && length(var.subnet_ids) > 0 && length(var.security_group_ids) > 0)
      error_message = "ECS needs real AWS, a worker image, subnets and security groups."
    }
    precondition {
      condition     = var.use_localstack || (startswith(var.producer_endpoint, "https://") && !strcontains(var.producer_endpoint, "example.com"))
      error_message = "Set a real, publicly reachable HTTPS producer_endpoint for AWS."
    }
  }
}
module "ecs" {
  count              = var.deploy_ecs ? 1 : 0
  source             = "./modules/ecs"
  name               = var.name
  region             = var.region
  worker_image       = var.worker_image
  subnet_ids         = var.subnet_ids
  security_group_ids = var.security_group_ids
  queue_arn          = module.sqs.queue_arn
  table_arn          = module.dynamodb.table_arn
  queue_name         = var.name
  table_name         = module.dynamodb.table_name
  depends_on         = [terraform_data.configuration]
}
