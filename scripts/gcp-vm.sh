#!/usr/bin/env bash
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/gcp-vm.sh PROJECT_ID [ZONE] [--apply]"
  echo "Default prints the command. --apply creates billable GCP resources."
  exit 1
fi
task_project="$1"
task_zone="${2:-asia-south1-a}"
task_mode="${3:---plan}"
task_vm="drone-parts-assistant"
task_machine="${GCP_MACHINE_TYPE:-e2-standard-2}"
command_args=(gcloud compute instances create "$task_vm"
  --project "$task_project" --zone "$task_zone" --machine-type "$task_machine"
  --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud
  --boot-disk-size 20GB --boot-disk-type pd-standard
  --no-service-account --no-scopes
  --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring
  --metadata-from-file "startup-script=$root_dir/deploy/gcp-startup.sh")
if [[ "$task_mode" == "--apply" ]]; then
  command "${command_args[@]}"
else
  printf 'Planned command (no cloud resources created):\n'
  printf '%q ' "${command_args[@]}"
  printf '\nReview the GCP cost estimate for this region before using --apply.\n'
fi
