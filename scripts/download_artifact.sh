#!/usr/bin/env bash

set -euo pipefail

destination_dir=""
download_cache=""
url=""
filename=""
expected_sha256=""

usage() {
    echo "Usage: $0 --destination DIRECTORY --download-cache DIRECTORY --url URL --filename NAME --sha256 HEX"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --destination) destination_dir="${2:-}"; shift 2 ;;
        --download-cache) download_cache="${2:-}"; shift 2 ;;
        --url) url="${2:-}"; shift 2 ;;
        --filename) filename="${2:-}"; shift 2 ;;
        --sha256) expected_sha256="${2:-}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

[[ -n "$destination_dir" && -n "$download_cache" && -n "$url" && -n "$filename" ]] || {
    usage >&2
    exit 2
}
[[ "$url" == https://* ]] || { echo "Artifact URL must use HTTPS" >&2; exit 2; }
[[ "$filename" != */* && "$filename" != "." && "$filename" != ".." ]] || {
    echo "Artifact filename must be a single safe path component" >&2
    exit 2
}
[[ "$expected_sha256" =~ ^[0-9a-f]{64}$ ]] || {
    echo "Artifact SHA-256 must contain 64 lowercase hexadecimal characters" >&2
    exit 2
}

for required_command in curl sha256sum cp mv rm; do
    command -v "$required_command" >/dev/null 2>&1 || {
        echo "Missing prerequisite: $required_command" >&2
        exit 3
    }
done

mkdir -p "$destination_dir" "$download_cache"
destination="${destination_dir}/${filename}"
partial="${download_cache}/${filename}.part"
publishing="${destination}.installing"
trap 'rm -f "$publishing"' EXIT

if [[ -f "$destination" ]]; then
    actual_sha256="$(sha256sum "$destination" | awk '{ print $1 }')"
    if [[ "$actual_sha256" == "$expected_sha256" ]]; then
        echo "Verified artifact already exists: ${destination}"
        exit 0
    fi
    echo "Existing artifact checksum does not match: ${destination}" >&2
    exit 1
fi

curl --fail --location --retry 3 --continue-at - --output "$partial" "$url"
actual_sha256="$(sha256sum "$partial" | awk '{ print $1 }')"
if [[ "$actual_sha256" != "$expected_sha256" ]]; then
    echo "Downloaded artifact checksum does not match." >&2
    echo "Expected: ${expected_sha256}" >&2
    echo "Actual:   ${actual_sha256}" >&2
    rm "$partial"
    exit 1
fi

cp "$partial" "$publishing"
mv "$publishing" "$destination"
rm "$partial"
echo "Installed and verified: ${destination}"
