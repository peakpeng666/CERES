#!/usr/bin/env bash
# Build and validate the checked-out revision before publishing any tag.
set -euo pipefail
cd "$(dirname "$0")/.."

name="${1:?bench or sim}"
dockerfile="${2:?dockerfile}"
[[ "$name" == bench || "$name" == sim ]] || { echo 'Expected bench or sim' >&2; exit 2; }
owner="$(printf '%s' "${GITHUB_REPOSITORY_OWNER:?}" | tr '[:upper:]' '[:lower:]')"
image="ghcr.io/${owner}/urp-${name}"
sha="${GITHUB_SHA:?}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'GITHUB_SHA must be a full commit SHA' >&2; exit 2; }
[[ "$(git rev-parse HEAD)" == "$sha" ]] || { echo 'Checkout and GITHUB_SHA disagree' >&2; exit 2; }
source_url="${SOURCE:-https://github.com/peakpeng666/URP}"

# Publish a commit-shaped lookup tag and export the actual content digest.
# Channel aliases are promoted only from the verified content digests.
tags=("$sha")

args=()
for tag in "${tags[@]}"; do
  args+=(-t "${image}:${tag}")
done

docker build -f "$dockerfile" \
  --build-arg "URP_REVISION=${sha}" \
  --label "org.opencontainers.image.source=${source_url}" \
  --label "org.opencontainers.image.revision=${sha}" \
  --label "org.opencontainers.image.title=urp-${name}" \
  "${args[@]}" .

bash deploy/test-image.sh "${image}:${sha}" "$sha" "$name" \
  "${URP_IMAGE_TEST_OUTPUT:-.local-state/image-${name}}"

# The commit-shaped tag can be overwritten. Capture the digest reported by
# this exact push, not a later resolution of that tag from the registry.
push_log="$(docker push "${image}:${sha}")"
printf '%s\n' "$push_log"
digest="$(printf '%s\n' "$push_log" | sed -nE 's/^.*digest: (sha256:[0-9a-f]{64})( size: [0-9]+)?$/\1/p')"
[[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Push did not return exactly one valid manifest digest' >&2; exit 1; }
if [[ -n "${GITHUB_OUTPUT:-}" ]]; then printf 'digest=%s\n' "$digest" >> "$GITHUB_OUTPUT"; fi
