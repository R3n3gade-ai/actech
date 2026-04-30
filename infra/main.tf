# achelion_arms/infra/main.tf
# Achelion ARMS: "The Fortress" Infrastructure Configuration
# AWS Cloud Architecture — ECS Fargate + EventBridge + RDS + Redis + S3 + SNS
# Using Terraform for Infrastructure as Code (IaC)

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# --- 1. Networking (VPC) ---
# A secure, private VPC with public and private subnets.
# Zero direct internet access to the Master Engine.
module "vpc" {
  source = "./modules/vpc"
  
  vpc_cidr             = "10.0.0.0/16"
  public_subnets       = ["10.0.1.0/24", "10.0.2.0/24"]
  private_subnets      = ["10.0.10.0/24", "10.0.11.0/24"]
  enable_nat_gateway   = true
}

# --- 2. State Management (RDS PostgreSQL) ---
# Holds the immutable audit_log, portfolio_snapshots, and mc_pmi_history.
module "rds" {
  source = "./modules/rds"
  
  vpc_id              = module.vpc.vpc_id
  private_subnets     = module.vpc.private_subnets
  instance_class      = var.rds_instance_class
  allocated_storage   = 20
  db_name             = "arms_fortress"
}

# --- 3. High-Speed Cache (ElastiCache Redis) ---
# Crucial for SYS-1 (Circuit Breakers) and SYS-5 (Correlation Monitor).
module "redis" {
  source = "./modules/redis"
  
  vpc_id              = module.vpc.vpc_id
  private_subnets     = module.vpc.private_subnets
  node_type           = "cache.t4g.micro"
  num_cache_nodes     = 1
}

# --- 4. Container Orchestration (ECS Fargate) ---
# The Master Engine (L4-L6) runs as a containerized service.
module "ecs" {
  source = "./modules/ecs"
  
  vpc_id              = module.vpc.vpc_id
  private_subnets     = module.vpc.private_subnets
  cluster_name        = "${var.project_name}-cluster-${var.environment}"
  container_image     = var.container_image
  cpu                 = var.ecs_cpu
  memory              = var.ecs_memory
}

# --- 5. Security & Secrets ---
# AWS Secrets Manager for API keys and database credentials.
resource "aws_secretsmanager_secret" "arms_secrets" {
  name = "${var.project_name}-${var.environment}-secrets"
}

# --- 6. S3 State Bucket ---
# Persistent state storage, backups, and report archive.
resource "aws_s3_bucket" "state_bucket" {
  bucket = "${var.project_name}-state-${var.environment}-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_versioning" "state_versioning" {
  bucket = aws_s3_bucket.state_bucket.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state_encryption" {
  bucket = aws_s3_bucket.state_bucket.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state_block_public" {
  bucket = aws_s3_bucket.state_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "state_lifecycle" {
  bucket = aws_s3_bucket.state_bucket.id

  rule {
    id     = "archive-old-backups"
    status = "Enabled"
    filter {
      prefix = "backups/"
    }
    transition {
      days          = 30
      storage_class = "GLACIER"
    }
    expiration {
      days = 365
    }
  }
}

data "aws_caller_identity" "current" {}

# --- 7. SNS Alert Topic ---
# Critical failure alerts sent to PM phone and email.
resource "aws_sns_topic" "arms_alerts" {
  name = "${var.project_name}-alerts-${var.environment}"
}

resource "aws_sns_topic_subscription" "pm_email" {
  topic_arn = aws_sns_topic.arms_alerts.arn
  protocol  = "email"
  endpoint  = var.pm_alert_email
}

# --- 8. CloudWatch Log Group ---
resource "aws_cloudwatch_log_group" "arms_logs" {
  name              = "/arms/${var.environment}"
  retention_in_days = 90
}

# --- 9. IAM Role for EventBridge Scheduler ---
resource "aws_iam_role" "scheduler_role" {
  name = "${var.project_name}-scheduler-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "scheduler.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "scheduler_ecs_policy" {
  name = "${var.project_name}-scheduler-ecs-${var.environment}"
  role = aws_iam_role.scheduler_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = ["*"]
      },
      {
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = ["*"]
      }
    ]
  })
}

# --- 10. EventBridge Scheduler Rules ---
# Each ARMS job is triggered by an EventBridge schedule that runs an ECS task.

# 10a. Full Morning Sweep — 5:15 AM CT Monday-Friday
resource "aws_scheduler_schedule" "morning_sweep" {
  name       = "${var.project_name}-morning-sweep-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(15 10 ? * MON-FRI *)"  # 5:15 AM CT = 10:15 UTC
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "full_morning_sweep"]
      }]
    })
  }
}

# 10b. EOD Snapshot — 2:50 PM CT Monday-Friday
resource "aws_scheduler_schedule" "eod_snapshot" {
  name       = "${var.project_name}-eod-snapshot-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(50 19 ? * MON-FRI *)"  # 2:50 PM CT = 19:50 UTC
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "eod_snapshot"]
      }]
    })
  }
}

# 10c. Market Data Ingest — Every 5 min (market hours guard in Python)
resource "aws_scheduler_schedule" "market_data" {
  name       = "${var.project_name}-market-data-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "rate(5 minutes)"
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "market_data_ingest"]
      }]
    })
  }
}

# 10d. Systematic Scan — Monday 5:00 AM CT
resource "aws_scheduler_schedule" "systematic_scan" {
  name       = "${var.project_name}-systematic-scan-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 10 ? * MON *)"  # 5:00 AM CT = 10:00 UTC
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "systematic_scan"]
      }]
    })
  }
}

# 10e. Database Backup — Nightly 2:30 AM CT
resource "aws_scheduler_schedule" "database_backup" {
  name       = "${var.project_name}-backup-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode                 = "FLEXIBLE"
    maximum_window_in_minutes = 15
  }

  schedule_expression          = "cron(30 7 ? * * *)"  # 2:30 AM CT = 7:30 UTC
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "database_backup"]
      }]
    })
  }
}

# 10f. Monthly Session Log Analytics — 1st of month 6:00 AM CT
resource "aws_scheduler_schedule" "session_analytics" {
  name       = "${var.project_name}-session-analytics-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 11 1 * ? *)"  # 6:00 AM CT 1st of month = 11:00 UTC
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "session_log_analytics"]
      }]
    })
  }
}

# 10g. Monthly Proactive Digest — 1st of month 7:00 AM CT
resource "aws_scheduler_schedule" "proactive_digest" {
  name       = "${var.project_name}-proactive-digest-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 12 1 * ? *)"  # 7:00 AM CT 1st of month = 12:00 UTC
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "proactive_digest"]
      }]
    })
  }
}

# 10h. Quarterly CCM Calibration — 1st of Jan, Apr, Jul, Oct at 4:00 AM CT
resource "aws_scheduler_schedule" "ccm_calibration" {
  name       = "${var.project_name}-ccm-calibration-${var.environment}"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(0 9 1 1,4,7,10 ? *)"  # 4:00 AM CT = 9:00 UTC
  schedule_expression_timezone = "America/Chicago"

  target {
    arn      = module.ecs.cluster_arn
    role_arn = aws_iam_role.scheduler_role.arn

    ecs_parameters {
      task_definition_arn = module.ecs.task_definition_arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = module.vpc.private_subnets
        assign_public_ip = false
      }
    }

    input = jsonencode({
      containerOverrides = [{
        name    = "arms-engine"
        command = ["python", "-m", "scheduling.master_scheduler", "ccm_calibration"]
      }]
    })
  }
}
