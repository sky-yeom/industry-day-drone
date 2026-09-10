terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }

  # Local state is fine for a single-operator demo, but it contains secrets
  # (ACR admin password, site PIN) in plain text — keep terraform.tfstate*
  # out of git (see .gitignore) and consider an azurerm remote backend if
  # more than one person manages this stack.
}

provider "azurerm" {
  features {}
}
