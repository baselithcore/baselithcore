locals {
  secret_name = "${var.release_name}-secrets"
}

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kube_context != "" ? var.kube_context : null
}

provider "helm" {
  kubernetes {
    config_path    = var.kubeconfig_path
    config_context = var.kube_context != "" ? var.kube_context : null
  }
}

resource "kubernetes_namespace" "this" {
  count = var.create_namespace ? 1 : 0

  metadata {
    name = var.namespace
    labels = {
      "app.kubernetes.io/part-of" = "baselithcore"
    }
  }
}

# Credentials live in a Terraform-managed Secret rather than in Helm values, so
# they never land in the Helm release manifest or state's plan output.
resource "kubernetes_secret" "app" {
  count = var.manage_secret && length(var.app_secrets) > 0 ? 1 : 0

  metadata {
    name      = local.secret_name
    namespace = var.namespace
  }

  type = "Opaque"
  # `data` takes plaintext values; the provider base64-encodes them for the API.
  data = var.app_secrets

  depends_on = [kubernetes_namespace.this]
}

resource "helm_release" "baselithcore" {
  name      = var.release_name
  namespace = var.namespace
  chart     = var.chart_path

  # Chart manages no secret; it references the Terraform-managed one.
  set {
    name  = "secrets.create"
    value = "false"
  }
  set {
    name  = "secrets.existingSecret"
    value = (var.manage_secret && length(var.app_secrets) > 0) ? local.secret_name : ""
  }

  dynamic "set" {
    for_each = var.image_tag != "" ? { tag = var.image_tag } : {}
    content {
      name  = "image.tag"
      value = set.value
    }
  }

  dynamic "set" {
    for_each = var.set_values
    content {
      name  = set.key
      value = set.value
    }
  }

  values = [for f in var.values_files : file(f)]

  depends_on = [
    kubernetes_namespace.this,
    kubernetes_secret.app,
  ]
}
