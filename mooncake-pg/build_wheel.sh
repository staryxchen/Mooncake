#!/bin/bash
# Script to build mooncake-pg wheel package
# Usage: ./build_wheel.sh [torch_version]
# Example: ./build_wheel.sh 2.8.0

set -e
set -x

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)

cd "${SCRIPT_DIR}"

# Clean previous build
echo "Cleaning previous build artifacts..."
rm -rf build/
rm -rf mooncake/*.so

# Detect CUDA version
CUDA_VERSION=${CUDA_VERSION:-$(nvcc --version 2>/dev/null | grep -o "release [0-9][0-9]*\.[0-9]*" | awk '{print $2}' || true)}
if [ -z "$CUDA_VERSION" ] && [ -f /usr/local/cuda/version.txt ]; then
    CUDA_VERSION=$(grep -Eo "[0-9]+\.[0-9]+" /usr/local/cuda/version.txt | head -n1)
fi
CUDA_VERSION=${CUDA_VERSION:-"12.0"}
echo "Detected CUDA version: ${CUDA_VERSION}"

# Detect Python command
if command -v python3 &> /dev/null; then
    PYTHON=python3
elif command -v python &> /dev/null; then
    PYTHON=python
else
    echo "Error: Python not found"
    exit 1
fi
echo "Using Python: ${PYTHON}"

# Set CUDA architecture if not specified (required when GPU not accessible during build)
if [ -z "$TORCH_CUDA_ARCH_LIST" ]; then
    export TORCH_CUDA_ARCH_LIST="7.0;7.5;8.0;8.6;8.9;9.0"
    echo "Using default TORCH_CUDA_ARCH_LIST: ${TORCH_CUDA_ARCH_LIST}"
fi

# Check engine.so exists
ENGINE_SO=$(find "${ROOT_DIR}/build/mooncake-integration" -name "engine.cpython-*.so" 2>/dev/null | head -n1)
if [ -z "$ENGINE_SO" ]; then
    echo "Error: engine.cpython-*.so not found in ${ROOT_DIR}/build/mooncake-integration"
    echo "Please build mooncake-integration first."
    exit 1
fi
echo "Found engine: ${ENGINE_SO}"

# Build for specified torch versions or current installed version
if [ -z "$PG_TORCH_VERSIONS" ]; then
    if [ -n "$1" ]; then
        PG_TORCH_VERSIONS="$1"
    fi
fi

if [ -z "$PG_TORCH_VERSIONS" ]; then
    echo "Building with current torch version..."
    $PYTHON setup.py build_ext --build-lib .
else
    echo "Building for torch versions: ${PG_TORCH_VERSIONS}"
    for version in ${PG_TORCH_VERSIONS//;/ }; do
        echo "Installing torch ${version}..."
        cuda_major=${CUDA_VERSION%%.*}
        if [ "$cuda_major" -ge 13 ]; then
            pip install torch==$version --index-url https://download.pytorch.org/whl/cu${cuda_major}0
        else
            pip install torch==$version
        fi
        $PYTHON setup.py build_ext --build-lib . --force
    done
fi

# Verify build output
SO_FILES=$(find mooncake -name "*.so" 2>/dev/null)
if [ -z "$SO_FILES" ]; then
    echo "Error: No .so files generated in mooncake/"
    exit 1
fi

echo "Build completed successfully!"
echo "Generated files:"
ls -la mooncake/*.so

echo ""
echo "To install into mooncake-wheel package:"
echo "  cp mooncake/*.so ../mooncake-wheel/mooncake/"
