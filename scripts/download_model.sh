#!/usr/bin/env bash

set -euo pipefail

model_revision="541d1f99c6b0c3cd0b11a95167540bb8edefd82b"
model_filename="parakeet-tdt-0.6b-v3.q8_0.gguf"
model_sha256="e3880d0aaaaf2c308ea2c35016b2b895c423eb3fda924c1b463d1c19b7f4d32e"
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination_dir="${project_root}/.runtime/models"

usage() {
    echo "Usage: $0 [--destination DIRECTORY]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --destination)
            [[ $# -ge 2 ]] || { echo "--destination requires a value" >&2; exit 2; }
            destination_dir="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

for required_command in curl sha256sum; do
    command -v "$required_command" >/dev/null 2>&1 || {
        echo "Missing prerequisite: $required_command" >&2
        exit 3
    }
done

mkdir -p "$destination_dir"
destination="${destination_dir}/${model_filename}"
partial="${destination}.part"
url="https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3/resolve/${model_revision}/${model_filename}"

if [[ -f "$destination" ]]; then
    existing_sha256="$(sha256sum "$destination" | awk '{ print $1 }')"
    if [[ "$existing_sha256" == "$model_sha256" ]]; then
        echo "Verified model already exists: ${destination}"
        exit 0
    fi
    echo "Existing model checksum does not match: ${destination}" >&2
    echo "Expected: ${model_sha256}" >&2
    echo "Actual:   ${existing_sha256}" >&2
    exit 1
fi

echo "Downloading ${model_filename} (${model_revision})..."
curl --fail --location --retry 3 --continue-at - --output "$partial" "$url"
actual_sha256="$(sha256sum "$partial" | awk '{ print $1 }')"
if [[ "$actual_sha256" != "$model_sha256" ]]; then
    echo "Downloaded model checksum does not match." >&2
    echo "Expected: ${model_sha256}" >&2
    echo "Actual:   ${actual_sha256}" >&2
    exit 1
fi
mv "$partial" "$destination"
echo "Installed and verified: ${destination}"

