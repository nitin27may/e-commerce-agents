# ============================================================
# E-Commerce Agents — Frontend Dockerfile
# Multi-stage build: deps → build → runtime
# ============================================================

FROM node:22-alpine AS base

# Install pnpm (pin v10.x — v11 errors on ignored build scripts)
RUN corepack enable && corepack prepare pnpm@10.15.0 --activate

# ── Dependencies ─────────────────────────────────────────────
FROM base AS deps
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile --ignore-scripts

# ── Build ────────────────────────────────────────────────────
FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NEXT_TELEMETRY_DISABLED=1
ENV CI=true

# No NEXT_PUBLIC_API_URL build arg. The browser calls this server's own origin
# and src/app/api/[...path]/route.ts forwards to ORCHESTRATOR_URL at runtime,
# so this image is not built against a particular backend. That is what lets
# one image be promoted from compose to Azure without a rebuild.
RUN pnpm build

# ── Runtime ──────────────────────────────────────────────────
FROM base AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

# Where the server-side /api/* proxy forwards to. Override per environment:
# a compose service name locally, an internal FQDN on Container Apps.
ENV ORCHESTRATOR_URL="http://localhost:8080"

CMD ["node", "server.js"]
