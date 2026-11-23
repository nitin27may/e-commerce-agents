#!/usr/bin/env bash
# Put the images a deployment needs into an Azure Container Registry.
#
#   scripts/azure/acr-images.sh import --registry <acr name> --tag <tag> [--stack python|dotnet]
#                               [--source ghcr.io/nitin27may/e-commerce-agents] [--only a,b] [--dry-run]
#   scripts/azure/acr-images.sh build  --registry <acr name> --tag <tag> [--stack python|dotnet] [--only a,b] [--dry-run]
#
#   import  copies the published images from GHCR (`az acr import`, server side, idempotent: an image already
#           in the registry at that tag is skipped).
#   build   builds from this working tree in ACR Tasks (`az acr build`, linux/amd64): the remote build, no local Docker.
#           Always rebuilds; the tag is yours to keep unique (azure-up.sh --build-from-source uses src-<sha>).
#
# The image set comes from scripts/azure/images.tsv (the same list .github/workflows/build-images.yml publishes), filtered
# to the stack: python = Python images + shared, dotnet = .NET images + shared. --dry-run prints every command and runs none.
# Nothing here is verified against a real registry yet; see docs/azure-deployment.md.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/../.."
# shellcheck source=scripts/azure/lib/common.sh
source scripts/azure/lib/common.sh

MODE="${1:-}"
case "$MODE" in import|build) shift ;; -h|--help) sed -n '2,15p' "$0"; exit 0 ;; *) die "first argument must be import or build" ;; esac

REGISTRY="" TAG="" STACK="python" SOURCE="ghcr.io/nitin27may/e-commerce-agents" ONLY=""
while [ $# -gt 0 ]; do
    case "$1" in
        --registry) REGISTRY="$2"; shift 2 ;;
        --tag) TAG="$2"; shift 2 ;;
        --stack) STACK="$2"; shift 2 ;;
        --source) SOURCE="$2"; shift 2 ;;
        --only) ONLY="$2"; shift 2 ;;
        --dry-run) AZURE_DRY_RUN=1; shift ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) die "unknown argument: $1" ;;
    esac
done
[ -n "$REGISTRY" ] || die "--registry is required (the ACR name, not the login server)"
[ -n "$TAG" ] || die "--tag is required"
case "$TAG" in latest|main) die "--tag $TAG floats; use vX.Y.Z, sha-<7> or src-<sha>" ;; esac
case "$STACK" in python|dotnet) ;; *) die "--stack must be python or dotnet" ;; esac
export AZURE_DRY_RUN

selected() {
    local name="$1"
    [ -n "$ONLY" ] || return 0
    case ",$ONLY," in *",$name,"*) return 0 ;; *) return 1 ;; esac
}

count=0
while IFS=$'\t' read -r name used_by context dockerfile build_args; do
    case "$name" in ''|'#'*) continue ;; esac
    [ "$used_by" = both ] || [ "$used_by" = "$STACK" ] || continue
    selected "$name" || continue
    count=$((count + 1))
    image="${name}:${TAG}"
    if [ "$MODE" = import ]; then
        if [ "$AZURE_DRY_RUN" != "1" ] && az acr repository show --name "$REGISTRY" --image "$image" >/dev/null 2>&1; then
            log "$image is already in $REGISTRY; skipping"
            continue
        fi
        run az acr import --name "$REGISTRY" --source "${SOURCE}/${image}" --image "$image" --force
    else
        # `az acr build --file` is relative to the build context, which is the positional argument.
        file="$dockerfile"
        [ "$context" = "." ] || file="${dockerfile#"$context"/}"
        args=()
        if [ -n "$build_args" ]; then
            IFS=';' read -ra pairs <<<"$build_args"
            for pair in "${pairs[@]}"; do args+=(--build-arg "$pair"); done
        fi
        run az acr build --registry "$REGISTRY" --image "$image" --platform linux/amd64 --file "$file" ${args[@]+"${args[@]}"} "$context"
    fi
done <scripts/azure/images.tsv
[ "$count" -gt 0 ] || die "no image matched (stack=$STACK only=${ONLY:-all})"
log "$MODE: $count image(s) for stack $STACK at tag $TAG"
