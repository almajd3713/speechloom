#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tool_environment="${project_root}/.runtime/tools"

python3 -m venv "$tool_environment"
"${tool_environment}/bin/pip" install \
    "cmake==3.31.10" \
    "ninja==1.13.0"

"${tool_environment}/bin/cmake" --version
"${tool_environment}/bin/ninja" --version
echo "Build tools installed at: ${tool_environment}"

