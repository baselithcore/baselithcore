variable "kubeconfig_path" {
  description = "Path to the kubeconfig used to reach the target cluster."
  type        = string
  default     = "~/.kube/config"
}

variable "kube_context" {
  description = "kubeconfig context to use (empty = current-context)."
  type        = string
  default     = ""
}

variable "namespace" {
  description = "Namespace to deploy BaselithCore into."
  type        = string
  default     = "baselithcore"
}

variable "create_namespace" {
  description = "Whether Terraform should create the namespace."
  type        = bool
  default     = true
}

variable "release_name" {
  description = "Helm release name."
  type        = string
  default     = "baselithcore"
}

variable "chart_path" {
  description = "Path to the BaselithCore Helm chart."
  type        = string
  default     = "../helm/baselithcore"
}

variable "image_tag" {
  description = "Container image tag to deploy (defaults to chart appVersion when empty)."
  type        = string
  default     = ""
}

variable "values_files" {
  description = "List of Helm values file paths to apply, in order."
  type        = list(string)
  default     = []
}

variable "set_values" {
  description = "Non-sensitive Helm values to set (map of key -> value)."
  type        = map(string)
  default     = {}
}

# Sensitive credentials -> rendered into a Terraform-managed Kubernetes Secret
# that the chart consumes via secrets.existingSecret. Keep these in a tfvars
# file excluded from version control, or source them from a secrets manager.
variable "app_secrets" {
  description = "Sensitive env vars (SECRET_KEY, DATA_ENCRYPTION_KEYS, DB_PASSWORD, *_API_KEY, ...)."
  type        = map(string)
  default     = {}
  sensitive   = true
}

variable "manage_secret" {
  description = "If true, Terraform creates the Secret referenced by the chart."
  type        = bool
  default     = true
}
