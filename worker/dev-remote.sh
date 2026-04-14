#!/usr/bin/env bash
# Launcher: `wrangler dev --remote` with CF Access service-token auth.
#
# Zone routes put `portal-api` behind Cloudflare Access. In a browser the
# Access cookie lets you in; in a CLI wrangler needs an Access service
# token instead. This script sources `worker/.env.access` (see
# `.env.access.example` for the dashboard steps), then execs wrangler.
#
# Usage: bash worker/dev-remote.sh [extra wrangler args]

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env.access ]; then
  echo "worker/.env.access missing."
  echo "Copy worker/.env.access.example to worker/.env.access and fill in the"
  echo "service-token Client ID + Client Secret — steps are in that file."
  exit 1
fi

# Export every VAR=value line (set -a) so they reach wrangler's subprocess.
set -a
# shellcheck disable=SC1091
source .env.access
set +a

if [ -z "${CLOUDFLARE_ACCESS_CLIENT_ID:-}" ] || [ -z "${CLOUDFLARE_ACCESS_CLIENT_SECRET:-}" ]; then
  echo "worker/.env.access is present but CLOUDFLARE_ACCESS_CLIENT_ID or"
  echo "CLOUDFLARE_ACCESS_CLIENT_SECRET is empty. Fill them in first."
  exit 1
fi

exec npx wrangler dev --remote "$@"
