resource "aws_kms_key" "db" {
  description             = "KMS key for CTMS RDS encryption (${var.environment})"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

resource "aws_db_instance" "this" {
  identifier        = "ctms-${var.environment}"
  engine            = "postgres"
  engine_version    = "15.4"
  instance_class    = var.instance_class
  allocated_storage = var.allocated_storage
  storage_encrypted = true
  kms_key_id        = aws_kms_key.db.arn
  storage_type      = var.storage_type
  iops              = var.iops
  multi_az          = var.multi_az
  db_name           = "ctms"
  username          = var.db_username

  manage_master_user_password   = true
  master_user_secret_kms_key_id = aws_kms_key.db.arn

  parameter_group_name   = aws_db_parameter_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  db_subnet_group_name   = var.db_subnet_group_name

  backup_retention_period   = var.backup_retention_days
  backup_window             = "03:00-04:00"
  maintenance_window        = "sun:04:00-sun:05:00"
  skip_final_snapshot       = false
  final_snapshot_identifier = "ctms-${var.environment}-final-${formatdate("YYYY-MM-DD", timestamp())}"

  enabled_cloudwatch_logs_exports     = ["postgresql"]
  deletion_protection                 = var.environment == "prod"
  iam_database_authentication_enabled = true
}

resource "aws_security_group" "db" {
  name        = "ctms-${var.environment}-db-sg"
  description = "CTMS RDS - inbound from app tier only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from app tier only"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.app_security_group_id]
  }
}

resource "aws_db_parameter_group" "this" {
  name   = "ctms-${var.environment}-pg"
  family = "postgres15"

  parameter { name = "max_connections"    value = var.max_connections }
  parameter { name = "shared_buffers"     value = "{DBInstanceClassMemory/2}" }
  parameter { name = "ssl"                value = "1" }
  parameter { name = "log_connections"    value = "1" }
  parameter { name = "log_disconnections" value = "1" }
  parameter { name = "log_statement"      value = "ddl" }
}