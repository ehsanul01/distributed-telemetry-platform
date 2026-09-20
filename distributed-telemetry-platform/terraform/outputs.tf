output "queue_url" { value = module.sqs.queue_url }
output "dlq_url" { value = module.sqs.dlq_url }
output "table_name" { value = module.dynamodb.table_name }
output "api_id" { value = module.api_gateway.api_id }
output "invoke_url" { value = module.api_gateway.invoke_url }
