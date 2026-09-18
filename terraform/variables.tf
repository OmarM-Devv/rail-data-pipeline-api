variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "eu-west-2"
}

variable "project_name" {
  description = "Name prefix applied to every resource."
  type        = string
  default     = "rail-data-pipeline-api"

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9-]{1,40}$", var.project_name))
    error_message = "project_name must be lowercase alphanumeric/hyphens, 2-41 chars."
  }
}

variable "environment" {
  description = "Deployment environment tag."
  type        = string
  default     = "production"
}

variable "admin_cidr" {
  description = "CIDR block allowed to reach SSH (port 22), e.g. \"203.0.113.10/32\". Must not be 0.0.0.0/0."
  type        = string

  validation {
    condition     = can(cidrhost(var.admin_cidr, 0)) && var.admin_cidr != "0.0.0.0/0"
    error_message = "admin_cidr must be a valid IPv4 CIDR and must not be 0.0.0.0/0."
  }
}

variable "app_port" {
  description = "Port the API container listens on and the security group exposes."
  type        = number
  default     = 8000
}

variable "instance_type" {
  description = "EC2 instance type. t3.micro (1 GiB) is Free Tier eligible and fits the ~2,600-row dataset."
  type        = string
  default     = "t3.micro"
}

variable "root_volume_size_gb" {
  description = "Root EBS volume size in GiB (Docker images and station data live here)."
  type        = number
  default     = 20
}

variable "ssh_key_name" {
  description = "Existing EC2 key pair name for SSH. Leave null to rely on SSM Session Manager only."
  type        = string
  default     = null
}

variable "github_owner" {
  description = "GitHub user or organisation that owns the repository."
  type        = string
  default     = "OmarM-Devv"
}

variable "github_repo" {
  description = "GitHub repository name allowed to assume the deploy role."
  type        = string
  default     = "rail-data-pipeline-api"
}

# The repo uses GitHub's immutable OIDC subject, which embeds numeric IDs:
#   repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:refs/heads/<branch>
# Look them up with: gh api repos/<owner>/<repo>/actions/oidc/customization/sub
# Set both to null to fall back to the legacy repo:<owner>/<repo> format.
variable "github_owner_id" {
  description = "Numeric GitHub owner ID used in the immutable OIDC subject claim."
  type        = number
  default     = 248359882
}

variable "github_repo_id" {
  description = "Numeric GitHub repository ID used in the immutable OIDC subject claim."
  type        = number
  default     = 1393259138
}

variable "github_deploy_branch" {
  description = "Only workflows running on this branch may assume the deploy role."
  type        = string
  default     = "main"
}

variable "create_github_oidc_provider" {
  description = "Create the GitHub OIDC provider. Set false if the account already has one (only one per URL is allowed)."
  type        = bool
  default     = true
}

variable "ecr_image_retention_count" {
  description = "Number of most recent images kept in ECR; older ones are expired. 3 stays under the 500 MB Free Tier."
  type        = number
  default     = 3
}
