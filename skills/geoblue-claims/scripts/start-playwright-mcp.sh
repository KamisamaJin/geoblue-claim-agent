#!/bin/sh
set -eu

cd "$(dirname "$0")/../../.."
umask 077
mkdir -p data/playwright-output

case "$#:${1-}" in
    0:) PLAYWRIGHT_MCP_HEADLESS=1 ;;
    1:--sign-in) PLAYWRIGHT_MCP_HEADLESS=0 ;;
    *) echo 'Usage: start-playwright-mcp.sh [--sign-in]' >&2; exit 2 ;;
esac
export PLAYWRIGHT_MCP_HEADLESS

package='@playwright/mcp@0.0.86'
if command -v npx >/dev/null 2>&1; then
    set -- npx --yes "$package"
elif command -v pnpm >/dev/null 2>&1; then
    set -- pnpm dlx "$package"
else
    echo 'Playwright MCP requires Node.js and npx or pnpm.' >&2
    exit 1
fi

# Keep the default browser profile in the local browser cache, outside the repo.
# Do not expose site-provided tools or write session traces to the project.
exec "$@" --browser chrome --caps vision --output-dir data/playwright-output \
    --console-level error --codegen none --no-webmcp \
    --redact-url-params code,state,access_token,refresh_token,id_token
