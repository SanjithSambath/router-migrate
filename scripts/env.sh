# Source this to keep every tool install inside the project folder.
_RM_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)"
export UV_PYTHON_INSTALL_DIR="$_RM_ROOT/.tools/python"
export UV_PYTHON_BIN_DIR="$_RM_ROOT/.tools/bin"
export UV_CACHE_DIR="$_RM_ROOT/.tools/uv-cache"
export UV_TOOL_DIR="$_RM_ROOT/.tools/uv-tools"
export UV_TOOL_BIN_DIR="$_RM_ROOT/.tools/bin"
export UV_NO_MODIFY_PATH=1
export npm_config_cache="$_RM_ROOT/.tools/npm-cache"
export PIP_CACHE_DIR="$_RM_ROOT/.tools/pip-cache"
export PATH="$_RM_ROOT/.venv/bin:$_RM_ROOT/node_modules/.bin:$PATH"
