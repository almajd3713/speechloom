#!/usr/bin/env bash

set -euo pipefail

runtime_ref="4f9676226f667d14608487df744f375db87127f8"
runtime_url="https://github.com/NVIDIA/NeMo-Speech.cpp.git"
backend="cpu"
with_nmt="OFF"
skip_gpu_check="false"
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
install_prefix="${project_root}/.runtime/nemo-speech"
tools_prefix="${project_root}/.runtime/tools"

usage() {
    echo "Usage: $0 [--backend cpu|cuda|vulkan] [--prefix DIRECTORY] [--tools-prefix DIRECTORY] [--with-nmt] [--skip-gpu-check]"
    echo "Relative prefixes are resolved from the repository root."
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --backend)
            [[ $# -ge 2 ]] || { echo "--backend requires a value" >&2; exit 2; }
            backend="$2"
            shift 2
            ;;
        --prefix)
            [[ $# -ge 2 ]] || { echo "--prefix requires a value" >&2; exit 2; }
            install_prefix="$2"
            shift 2
            ;;
        --tools-prefix)
            [[ $# -ge 2 ]] || { echo "--tools-prefix requires a value" >&2; exit 2; }
            tools_prefix="$2"
            shift 2
            ;;
        --with-nmt)
            with_nmt="ON"
            shift
            ;;
        --skip-gpu-check)
            skip_gpu_check="true"
            shift
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

if [[ -x "${tools_prefix}/bin/cmake" ]]; then
    export PATH="${tools_prefix}/bin:${PATH}"
fi

if [[ "$install_prefix" != /* ]]; then
    install_prefix="${project_root}/${install_prefix#./}"
fi

case "$backend" in
    cpu|cuda|vulkan) ;;
    *) echo "Unsupported backend: $backend" >&2; exit 2 ;;
esac

for required_command in git cmake ninja cc c++; do
    command -v "$required_command" >/dev/null 2>&1 || {
        echo "Missing prerequisite: $required_command" >&2
        echo "NeMo-Speech.cpp requires CMake 3.26+, Ninja, and a C++17 compiler." >&2
        exit 3
    }
done

cmake_version="$(cmake --version | awk 'NR == 1 { print $3 }')"
cmake_major="${cmake_version%%.*}"
cmake_rest="${cmake_version#*.}"
cmake_minor="${cmake_rest%%.*}"
if (( cmake_major < 3 || (cmake_major == 3 && cmake_minor < 26) || cmake_major >= 4 )); then
    echo "CMake 3.26 through 3.x is required; found ${cmake_version}." >&2
    echo "Run scripts/bootstrap_build_tools.sh for an isolated compatible toolchain." >&2
    exit 3
fi

if [[ "$backend" == "cuda" ]]; then
    if [[ "$skip_gpu_check" != "true" ]]; then
        command -v nvidia-smi >/dev/null 2>&1 || {
            echo "CUDA requested but nvidia-smi is unavailable." >&2
            exit 3
        }
        nvidia-smi >/dev/null || {
            echo "CUDA requested but nvidia-smi cannot communicate with the GPU." >&2
            exit 3
        }
    fi
    command -v nvcc >/dev/null 2>&1 || {
        echo "CUDA source builds require nvcc from a supported CUDA toolkit." >&2
        exit 3
    }
fi

temporary_root="$(mktemp -d)"
trap 'rm -rf "$temporary_root"' EXIT
source_dir="${temporary_root}/NeMo-Speech.cpp"

echo "Fetching NeMo-Speech.cpp at ${runtime_ref}..."
git clone --filter=blob:none "$runtime_url" "$source_dir"
git -C "$source_dir" checkout --detach "$runtime_ref"
git -C "$source_dir" submodule update --init ggml llama.cpp third_party/cpp-httplib

preset="${backend}-server"
echo "Building ${preset}..."
(
    cd "$source_dir"
    scripts/build_sentencepiece_static.sh
    scripts/configure.sh "$preset" \
        -DNEMO_SPEECH_BUILD_TTS=OFF \
        -DNEMO_SPEECH_BUILD_NMT="$with_nmt" \
        -DNEMO_SPEECH_WITH_NMT="$with_nmt" \
        -DNEMO_SPEECH_BUILD_HTTP=OFF
    cmake --build --preset "$preset"
    cmake --install "build/${preset}" --prefix "$install_prefix"
)

"${install_prefix}/bin/nemo-speech" --version
echo
echo "Runtime installed at: ${install_prefix}"
echo "Configure nemo_speech = ${install_prefix}/bin/nemo-speech"
