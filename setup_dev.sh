#!/bin/bash
set -e

# Configuration
DEFAULT_PYTHON_VERSION="3.11"
PYTHON_VERSION="${1:-$DEFAULT_PYTHON_VERSION}"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== UHD Development Setup ===${NC}"
echo -e "${BLUE}Target Python Version: ${PYTHON_VERSION}${NC}"

# 1. Ensure uv is installed
if ! command -v uv &> /dev/null; then
    echo "uv not found. Installing..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    
    # Add uv to PATH for this session if it was just installed
    if [ -d "$HOME/.local/bin" ]; then
        export PATH="$HOME/.local/bin:$PATH"
    fi
    if [ -d "$HOME/.cargo/bin" ]; then
        export PATH="$HOME/.cargo/bin:$PATH"
    fi
    
    if ! command -v uv &> /dev/null; then
        echo "Error: uv installed but not found in PATH. Please restart your shell or add it manually."
        exit 1
    fi
else
    echo "Found uv: $(uv --version)"
fi

# 2. Create Virtual Environment with specific Python version
# uv will automatically download and install this python version if managed/missing
echo -e "${BLUE}Creating virtual environment with Python ${PYTHON_VERSION}...${NC}"
uv venv .venv --python "${PYTHON_VERSION}" --seed

# 3. Install Dependencies
# We use the python binary inside the venv to ensure we are targeting the right environment
VENV_PYTHON=".venv/bin/python"

echo -e "${BLUE}Installing dependencies (including cmake/ninja)...${NC}"
# Install dependencies from pyproject.toml
uv pip install --python "$VENV_PYTHON" -e .[dev]

echo -e "${GREEN}=== Setup Complete ===${NC}"
echo -e "Activate your environment with:"
echo -e "  ${GREEN}source .venv/bin/activate${NC}"
echo -e "Tools available:"
echo -e "  - ${GREEN}uv${NC} (Package Manager)"
echo -e "  - ${GREEN}ruff${NC} (Linter)"
echo -e "  - ${GREEN}mypy${NC} (Type Checker)"
echo -e "  - ${GREEN}cmake${NC} & ${GREEN}ninja${NC} (Build Tools)"