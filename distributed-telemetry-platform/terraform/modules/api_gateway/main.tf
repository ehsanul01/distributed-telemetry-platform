variable "name" { type = string }
variable "producer_endpoint" { type = string }
resource "aws_api_gateway_rest_api" "api" { name = var.name }
resource "aws_api_gateway_resource" "ingest" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  parent_id   = aws_api_gateway_rest_api.api.root_resource_id
  path_part   = "ingest"
}
resource "aws_api_gateway_method" "post" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  resource_id   = aws_api_gateway_resource.ingest.id
  http_method   = "POST"
  authorization = "NONE"
}
resource "aws_api_gateway_integration" "producer" {
  rest_api_id             = aws_api_gateway_rest_api.api.id
  resource_id             = aws_api_gateway_resource.ingest.id
  http_method             = aws_api_gateway_method.post.http_method
  integration_http_method = "POST"
  type                    = "HTTP_PROXY"
  uri                     = "${trimsuffix(var.producer_endpoint, "/")}/ingest"
}
resource "aws_api_gateway_deployment" "api" {
  rest_api_id = aws_api_gateway_rest_api.api.id
  triggers    = { redeployment = sha1(jsonencode([aws_api_gateway_resource.ingest.id, aws_api_gateway_method.post.id, aws_api_gateway_integration.producer])) }
  lifecycle { create_before_destroy = true }
}
resource "aws_api_gateway_stage" "local" {
  rest_api_id   = aws_api_gateway_rest_api.api.id
  deployment_id = aws_api_gateway_deployment.api.id
  stage_name    = "v1"
}
output "api_id" { value = aws_api_gateway_rest_api.api.id }
output "invoke_url" { value = aws_api_gateway_stage.local.invoke_url }
