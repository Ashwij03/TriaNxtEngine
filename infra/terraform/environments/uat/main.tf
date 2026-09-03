module "db" {
  source                 = "../../modules/rds"
  environment            = var.environment
  instance_class         = var.instance_class
  allocated_storage      = var.allocated_storage
  storage_type           = var.storage_type
  iops                   = var.iops
  multi_az               = var.multi_az
  db_username            = var.db_username
  db_subnet_group_name   = var.db_subnet_group_name
  vpc_id                 = var.vpc_id
  app_security_group_id  = var.app_security_group_id
  backup_retention_days  = var.backup_retention_days
  max_connections        = var.max_connections
}

output "db_instance_endpoint"   { value = module.db.db_instance_endpoint }
output "master_user_secret_arn" { value = module.db.master_user_secret_arn }