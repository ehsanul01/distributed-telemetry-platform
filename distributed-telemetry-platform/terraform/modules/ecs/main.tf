# Optional worker deployment into an existing VPC. Supply an existing image.
variable "name" { type = string }
variable "region" { type = string }
variable "worker_image" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "queue_arn" { type = string }
variable "table_arn" { type = string }
variable "queue_name" { type = string }
variable "table_name" { type = string }
resource "aws_ecs_cluster" "worker" { name = var.name }
resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name}"
  retention_in_days = 14
}
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role" "task" {
  name               = "${var.name}-task"
  assume_role_policy = aws_iam_role.execution.assume_role_policy
}
resource "aws_iam_role_policy" "task" {
  role = aws_iam_role.task.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["sqs:GetQueueUrl", "sqs:GetQueueAttributes", "sqs:ReceiveMessage", "sqs:DeleteMessage"], Resource = var.queue_arn },
    { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:DescribeTable"], Resource = var.table_arn }
  ] })
}
resource "aws_ecs_task_definition" "worker" {
  family                   = var.name
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name             = "worker", image = var.worker_image, essential = true,
    environment      = [{ name = "AWS_DEFAULT_REGION", value = var.region }, { name = "QUEUE_NAME", value = var.queue_name }, { name = "TABLE_NAME", value = var.table_name }],
    healthCheck      = { command = ["CMD", "/worker", "healthcheck"], interval = 30, timeout = 5, retries = 3, startPeriod = 30 },
    logConfiguration = { logDriver = "awslogs", options = { "awslogs-group" = aws_cloudwatch_log_group.worker.name, "awslogs-region" = var.region, "awslogs-stream-prefix" = "worker" } }
  }])
}
resource "aws_ecs_service" "worker" {
  name            = var.name
  cluster         = aws_ecs_cluster.worker.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }
  depends_on = [aws_iam_role_policy_attachment.execution, aws_iam_role_policy.task]
}
