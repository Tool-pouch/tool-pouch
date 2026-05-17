#!/bin/sh
# Vercel build step: inject Supabase env vars into the static site.
#
# Reads SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY from the environment
# (set in Vercel project settings) and substitutes the {{TEMPLATE}}
# placeholders in index.html. RLS is what protects the data, so the
# publishable key is safe to ship to the browser.
#
# Runs from this directory (Vercel "Root Directory" is set to `site/`).
set -e

: "${SUPABASE_URL:?SUPABASE_URL env var is required (set in Vercel project settings)}"
: "${SUPABASE_PUBLISHABLE_KEY:?SUPABASE_PUBLISHABLE_KEY env var is required (set in Vercel project settings)}"

# Portable in-place replace (works on both BSD/macOS and GNU/Linux sed).
# Use | as delimiter since neither URLs nor keys contain it.
tmp=$(mktemp)
sed "s|{{SUPABASE_URL}}|$SUPABASE_URL|g; s|{{SUPABASE_PUBLISHABLE_KEY}}|$SUPABASE_PUBLISHABLE_KEY|g" \
    index.html > "$tmp"
mv "$tmp" index.html

echo "Injected Supabase env vars into index.html"
