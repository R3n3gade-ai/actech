# achelion_arms/infra/variables.tf

variable "aws_region" {
  description = "The AWS region to deploy the infrastructure to."
  default     = "us-east-1"
}

variable "environment" {
  description = "The name of the environment (e.g., prod, dev)."
  default     = "prod"
}

variable "project_name" {
  description = "The name of the project."
  default     = "arms"
}

variable "container_image" {
  description = "Docker image URI for the ARMS engine container."
  default     = "achelion/arms-master:latest"
}

variable "ecs_cpu" {
  description = "CPU units for ECS Fargate tasks (1 vCPU = 1024)."
  type        = number
  default     = 1024
}

variable "ecs_memory" {
  description = "Memory (MiB) for ECS Fargate tasks."
  type        = number
  default     = 2048
}

variable "rds_instance_class" {
  description = "RDS PostgreSQL instance class."
  default     = "db.t4g.small"
}

variable "pm_alert_email" {
  description = "Email address for PM critical failure alerts via SNS."
  type        = string
  sensitive   = true
}
