#!/usr/bin/env bash
# Consume only digests exported by this run's successful anonymous verification.
# Workflow callers share one repository-wide channel-promotion lock.
set -euo pipefail
owner="$(printf '%s' "${GITHUB_REPOSITORY_OWNER:?}" | tr '[:upper:]' '[:lower:]')"
sha="${GITHUB_SHA:?}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || { echo 'GITHUB_SHA must be a full commit SHA' >&2; exit 2; }
tags=()
if [[ "${GITHUB_REF:-}" == refs/heads/main ]]; then
  tags=(main)
elif [[ "${GITHUB_REF:-}" == refs/tags/v* ]]; then
  # Keep version channels independent. Do not let an older/retried release
  # overwrite :latest: the repository has no canonical v* version ordering.
  tags=("${GITHUB_REF#refs/tags/}")
else
  exit 0
fi
for tag in "${tags[@]}"; do
  [[ "$tag" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || { echo 'Invalid image tag' >&2; exit 2; }
done
bench="${BENCH_DIGEST:-}"
sim="${SIM_DIGEST:-}"
for digest in "$bench" "$sim"; do
  [[ "$digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { echo 'Missing or invalid verified digest' >&2; exit 2; }
done

# Revalidate both accepted manifests before changing either public channel.
for name in bench sim; do
  digest="$bench"
  [[ "$name" != sim ]] || digest="$sim"
  source="ghcr.io/${owner}/urp-${name}@${digest}"
  docker pull "$source"
  actual="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$source")"
  [[ "$actual" == "$sha" ]] || { echo "Source revision mismatch for ceres-${name}" >&2; exit 1; }
done

# A rerun of an old main commit must not roll the channel backwards. All managed
# callers hold the same workflow job lock, including reusable workflows.
if [[ "${GITHUB_REF:-}" == refs/heads/main ]]; then
  remote="${SOURCE:-https://github.com/peakpeng666/URP}"
  head="$(git ls-remote --exit-code "$remote" refs/heads/main)"
  [[ "$head" == "$sha"$'\trefs/heads/main' ]] || {
    echo 'Skipping stale main promotion: source is no longer the remote main head.'
    exit 0
  }
fi

for name in bench sim; do
  digest="$bench"
  [[ "$name" != sim ]] || digest="$sim"
  image="ghcr.io/${owner}/urp-${name}"
  for tag in "${tags[@]}"; do
    # A registry-side copy from the verified digest cannot be redirected by a
    # concurrent rewrite of the source SHA tag. Preserve a single manifest.
    docker buildx imagetools create --prefer-index=false --tag "${image}:${tag}" "${image}@${digest}"
    printf 'Promoted %s:%s from %s\n' "$image" "$tag" "$digest"
  done
done
