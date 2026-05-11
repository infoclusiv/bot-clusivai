#!/usr/bin/env bash
set -euo pipefail

port="${PORT:-${SERVER_PORT:-5000}}"
public_url="${PUBLIC_WEBAPP_URL:-${WEBAPP_URL:-}}"
ngrok_bin="${NGROK_BIN:-ngrok}"

if [[ -z "$public_url" ]]; then
    echo "PUBLIC_WEBAPP_URL or WEBAPP_URL is required to start ngrok" >&2
    exit 1
fi

if [[ "$public_url" != https://* ]]; then
    echo "PUBLIC_WEBAPP_URL must be an https URL" >&2
    exit 1
fi

if ! command -v "$ngrok_bin" >/dev/null 2>&1; then
    echo "ngrok binary not found: $ngrok_bin" >&2
    exit 1
fi

# Matar instancias previas para liberar el endpoint en los servidores de ngrok
pkill -f "$ngrok_bin" 2>/dev/null || true
sleep 1

args=(http "127.0.0.1:${port}" --url "$public_url")

if [[ -n "${NGROK_AUTHTOKEN:-}" ]]; then
    args+=(--authtoken "$NGROK_AUTHTOKEN")
fi

if [[ "${NGROK_POOLING_ENABLED:-false}" == "true" ]]; then
    args+=(--pooling-enabled=true)
fi

echo "Starting ngrok: ${public_url} -> http://127.0.0.1:${port}"
exec "$ngrok_bin" "${args[@]}"