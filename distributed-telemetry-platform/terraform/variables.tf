variable "region" {
  type    = string
  default = "us-east-1"
}
variable "use_localstack" {
  type    = bool
  default = true
}
variable "localstack_endpoint" {
  type    = string
  default = "http://localhost:4566"
}
variable "name" {
  type    = string
  default = "telemetry-events"
}
variable "producer_endpoint" {
  type    = string
  default = "http://producer:8000"
}
variable "max_receive_count" {
  type    = number
  default = 5
  validation {
    condition     = var.max_receive_count >= 1 && floor(var.max_receive_count) == var.max_receive_count
    error_message = "max_receive_count must be a positive integer."
  }
}

variable "deploy_ecs" {
  type    = bool
  default = false
}
variable "worker_image" {
  type    = string
  default = ""
}
variable "subnet_ids" {
  type    = list(string)
  default = []
}
variable "security_group_ids" {
  type    = list(string)
  default = []
}
