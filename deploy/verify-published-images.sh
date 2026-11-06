#!/usr/bin/env bash
# Verify exactly the manifests produced by this run, without registry credentials.
set -euo pipefail
owner="$(printf '%s' "${GITHUB_REPOSITORY_OWNER:?}" | tr '[:upper:]' '[:lower:]')"
sha="${GITHUB_SHA:?}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'Invalid source SHA' >&2; exit 2; }
bench="${BENCH_DIGEST:-}"
sim="${SIM_DIGEST:-}"
for digest in "$bench" "$sim"; do
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Missing or invalid build digest' >&2; exit 2; }
done
export DOCKER_CONFIG="$(mktemp -d)"
trap 'rm -rf "$DOCKER_CONFIG"' EXIT
for name in bench sim; do
  digest="$bench"
  [[ "$name" != sim ]] || digest="$sim"
  source="ghcr.io/${owner}/urp-${name}@${digest}"
  docker pull "$source"
  actual="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$source")"
  [[ "$actual" == "$sha" ]] || { echo "Published revision mismatch for urp-${name}" >&2; exit 1; }
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then printf 'Verified anonymous source: %s\n' "$source" >> "$GITHUB_STEP_SUMMARY"; fi
done
# Export only after both exact manifests passed. Never resolve a SHA tag here.
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'bench_digest=%s\nsim_digest=%s\n' "$bench" "$sim" >> "$GITHUB_OUTPUT"
fi
