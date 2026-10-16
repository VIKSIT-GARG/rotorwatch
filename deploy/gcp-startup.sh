#!/usr/bin/env bash
set -euo pipefail
# Ubuntu 24.04 Compute Engine startup script. Installs the container runtime only.
# Tokens and application code are never placed in instance metadata.
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 unzip
systemctl enable --now docker
install -d -m 0755 /opt/drone-parts-assistant
