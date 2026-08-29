#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_environment="${project_root}/.runtime/tools"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix)
            [[ $# -ge 2 ]] || { echo "--prefix requires a value" >&2; exit 2; }
            tool_environment="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [--prefix DIRECTORY]"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ "$tool_environment" != /* ]]; then
    tool_environment="${project_root}/${tool_environment#./}"
fi

if ! python3 -m venv "$tool_environment"; then
    echo "Unable to create the build-tools virtual environment." >&2
    echo "On Debian/Ubuntu, install the version-matched python3-venv package and retry." >&2
    exit 1
fi
"${tool_environment}/bin/pip" install \
    "cmake==3.31.10" \
    "ninja==1.13.0"

"${tool_environment}/bin/cmake" --version
"${tool_environment}/bin/ninja" --version
echo "Build tools installed at: ${tool_environment}"
