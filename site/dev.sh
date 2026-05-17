#!/bin/sh
# Local dev for the site with the waitlist form actually working.
#
# Usage:
#   cp site/.env.local.example site/.env.local   # then fill in Supabase creds
#   sh site/dev.sh                                # http://localhost:8000
#
# Mirrors what Vercel does (env-var injection) but into a throwaway
# temp dir, so site/index.html on disk stays untouched.
set -e

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$HERE"

if [ ! -f .env.local ]; then
  echo "Error: site/.env.local not found." >&2
  echo "  cp site/.env.local.example site/.env.local, then fill in the values." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1091
. ./.env.local
set +a

: "${SUPABASE_URL:?SUPABASE_URL not set in .env.local}"
: "${SUPABASE_PUBLISHABLE_KEY:?SUPABASE_PUBLISHABLE_KEY not set in .env.local}"

OUT=$(mktemp -d)
trap 'rm -rf "$OUT"' EXIT INT TERM

# Copy site assets into a temp dir; strip dev-only files so they
# never get served accidentally.
cp -R . "$OUT/"
rm -f "$OUT"/.env* "$OUT"/dev.sh "$OUT"/build.sh "$OUT"/vercel.json

sed "s|{{SUPABASE_URL}}|$SUPABASE_URL|g; s|{{SUPABASE_PUBLISHABLE_KEY}}|$SUPABASE_PUBLISHABLE_KEY|g" \
    index.html > "$OUT/index.html"

echo "Local site ready at http://localhost:8000 (Ctrl+C to stop)"
python3 -m http.server 8000 -d "$OUT" --bind 127.0.0.1
