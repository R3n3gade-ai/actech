# achelion_arms/infra/outputs.tf

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnets" {
  value = module.vpc.private_subnets
}

output "ecs_cluster_name" {
  value = module.ecs.cluster_name
}

output "rds_endpoint" {
  value     = module.rds.db_instance_endpoint
  sensitive = true
}

output "redis_endpoint" {
  value = module.redis.cache_nodes
}

output "state_bucket" {
  value = aws_s3_bucket.state_bucket.id
}

output "sns_alert_topic_arn" {
  value = aws_sns_topic.arms_alerts.arn
}

output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.arms_logs.name
}
