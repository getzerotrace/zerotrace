resource "aws_db_instance" "payments" {
  engine                  = "postgres"
  username                = "payments"
  backup_retention_period = 7     # the demo's one planted mistake is the password below
  master_password         = "{{gen:password}}"
}
