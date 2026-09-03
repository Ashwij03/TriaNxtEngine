terraform {
  backend "s3" {
    bucket         = "ctms-terraform-state"
    key            = "uat/db.tfstate"
    region         = "us-east-1"
    dynamodb_table = "ctms-terraform-locks"
    encrypt        = true
  }
}