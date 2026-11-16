#!/usr/bin/env bash
# The variables docker-compose.mixed.yml needs, and the compose files each mixed variant layers.
#
#   source scripts/lib/mixed-env.sh
#   mixed_env py-net      # exports MIXED_CONTEXT and MIXED_DOCKERFILE_*; sets MIXED_BASE_FILE
#   docker compose -f "$MIXED_BASE_FILE" -f docker-compose.mixed.yml ... up --build
#
# py-net: Python orchestrator in front of .NET specialists (base docker-compose.yml).
# net-py: .NET orchestrator in front of Python specialists (base docker-compose.dotnet.yml).

mixed_env() {
  local variant="${1:-}"
  case "$variant" in
    py-net)
      MIXED_BASE_FILE="docker-compose.yml"
      export MIXED_CONTEXT="."
      export MIXED_DOCKERFILE_PRODUCT_DISCOVERY="agents/dotnet/src/ECommerceAgents.ProductDiscovery/Dockerfile"
      export MIXED_DOCKERFILE_ORDER_MANAGEMENT="agents/dotnet/src/ECommerceAgents.OrderManagement/Dockerfile"
      export MIXED_DOCKERFILE_PRICING_PROMOTIONS="agents/dotnet/src/ECommerceAgents.PricingPromotions/Dockerfile"
      export MIXED_DOCKERFILE_REVIEW_SENTIMENT="agents/dotnet/src/ECommerceAgents.ReviewSentiment/Dockerfile"
      export MIXED_DOCKERFILE_INVENTORY_FULFILLMENT="agents/dotnet/src/ECommerceAgents.InventoryFulfillment/Dockerfile"
      ;;
    net-py)
      MIXED_BASE_FILE="docker-compose.dotnet.yml"
      export MIXED_CONTEXT="./agents/python"
      export MIXED_DOCKERFILE_PRODUCT_DISCOVERY="Dockerfile"
      export MIXED_DOCKERFILE_ORDER_MANAGEMENT="Dockerfile"
      export MIXED_DOCKERFILE_PRICING_PROMOTIONS="Dockerfile"
      export MIXED_DOCKERFILE_REVIEW_SENTIMENT="Dockerfile"
      export MIXED_DOCKERFILE_INVENTORY_FULFILLMENT="Dockerfile"
      ;;
    *)
      echo "mixed_env: unknown variant '$variant' (expected py-net or net-py)" >&2
      return 2
      ;;
  esac
  export MIXED_BASE_FILE MIXED="$variant"
}
