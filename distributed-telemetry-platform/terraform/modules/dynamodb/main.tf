variable "name" { type = string }
resource "aws_dynamodb_table" "events" {
  name         = var.name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"
  attribute {
    name = "event_id"
    type = "S"
  }
}
output "table_name" { value = aws_dynamodb_table.events.name }
output "table_arn" { value = aws_dynamodb_table.events.arn }
