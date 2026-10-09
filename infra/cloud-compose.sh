#!/usr/bin/env bash
# Run Compose with writable Buildx state and the managed Docker proxy/trust.
set -euo pipefail

splash_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
splash_buildx_config="$splash_root/.splash/buildx"

umask 077
mkdir -p "$splash_buildx_config"
chmod 700 "$splash_buildx_config"
# Preserve DOCKER_CONFIG, registry authentication, proxy defaults, and context.
# Buildx supports its own metadata directory independently of that configuration.
export BUILDX_CONFIG="$splash_buildx_config"
export SPLASH_RUNTIME_UID="${SPLASH_RUNTIME_UID:-$(id -u)}"
export SPLASH_RUNTIME_GID="${SPLASH_RUNTIME_GID:-$(id -g)}"

cd "$splash_root"
exec docker compose -f compose.yaml -f infra/compose.cloud.yaml "$@"
