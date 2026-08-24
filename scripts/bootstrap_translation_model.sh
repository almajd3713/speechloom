#!/usr/bin/env bash

set -euo pipefail

runtime_ref="4f9676226f667d14608487df744f375db87127f8"
runtime_url="https://github.com/NVIDIA/NeMo-Speech.cpp.git"
model_id="nvidia/Riva-Translate-4B-Instruct-v2"
model_revision="040d958b128018ff0bed2542a7b51005e9ea563c"
model_filename="riva-translate-4b-instruct-v2.q8_0.gguf"
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
destination_dir="${project_root}/.runtime/models"
converter_env="${project_root}/.runtime/nmt-converter"
cache_dir="${project_root}/.runtime/cache/huggingface"

usage() {
    echo "Usage: $0 [--destination DIRECTORY]"
    echo "Downloads the pinned 4B checkpoint and converts it locally to Q8_0 GGUF."
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

if [[ "$destination_dir" != /* ]]; then
    destination_dir="${project_root}/${destination_dir#./}"
fi

for required_command in git python3 sha256sum; do
    command -v "$required_command" >/dev/null 2>&1 || {
        echo "Missing prerequisite: $required_command" >&2
        exit 3
    }
done

mkdir -p "$destination_dir" "$(dirname "$converter_env")" "$cache_dir"
destination="${destination_dir}/${model_filename}"
if [[ -f "$destination" ]]; then
    echo "Translation model already exists: ${destination}"
    sha256sum "$destination"
    exit 0
fi

available_kib="$(df -Pk "$destination_dir" | awk 'NR == 2 { print $4 }')"
required_kib=$((16 * 1024 * 1024))
if (( available_kib < required_kib )); then
    echo "At least 16 GiB free is required for the checkpoint, converter, and GGUF." >&2
    exit 3
fi

if [[ ! -x "${converter_env}/bin/python" ]]; then
    python3 -m venv "$converter_env"
fi

temporary_root="$(mktemp -d)"
trap 'rm -rf "$temporary_root"' EXIT
source_dir="${temporary_root}/NeMo-Speech.cpp"

echo "Fetching the pinned NeMo converter..."
git clone --filter=blob:none "$runtime_url" "$source_dir"
git -C "$source_dir" checkout --detach "$runtime_ref"
git -C "$source_dir" submodule update --init llama.cpp

echo "Installing isolated converter dependencies..."
"${converter_env}/bin/python" -m pip install \
    -r "${source_dir}/requirements.txt" \
    -r "${source_dir}/llama.cpp/requirements/requirements-convert_hf_to_gguf.txt"

partial="${destination}.part"
echo "Downloading ${model_id}@${model_revision} and converting to Q8_0..."
"${converter_env}/bin/python" "${source_dir}/convert_model.py" "$model_id" \
    --architecture nmt \
    --revision "$model_revision" \
    --cache-dir "$cache_dir" \
    --outtype q8_0 \
    --outfile "$partial"
mv "$partial" "$destination"
sha256sum "$destination" | tee "${destination}.sha256"
echo "Installed translation model: ${destination}"
