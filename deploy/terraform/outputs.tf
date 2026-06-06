output "namespace" {
  description = "Namespace BaselithCore was deployed into."
  value       = var.namespace
}

output "release_name" {
  description = "Helm release name."
  value       = helm_release.baselithcore.name
}

output "release_status" {
  description = "Status of the Helm release."
  value       = helm_release.baselithcore.status
}

output "app_version" {
  description = "Deployed application version (chart appVersion)."
  value       = helm_release.baselithcore.metadata[0].app_version
}
