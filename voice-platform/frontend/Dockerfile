# ============================================================
#  STL Digital Voice Agent — Hardened Frontend Dockerfile
#  Multi-stage build, minimal attack surface, non-root runner
# ============================================================

# Stage 1: Build Dependencies & Next.js Bundle
FROM node:20-alpine AS builder

WORKDIR /app

# Install curl for any build tools
RUN apk add --no-cache libc6-compat

# Copy package descriptors & install dependencies strictly
COPY package.json package-lock.json ./
RUN npm ci

# Copy source code and build standalone production bundle
COPY . .
RUN npm run build

# Stage 2: Production Minimal Runner
FROM node:20-alpine AS runner
WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME="0.0.0.0"

# Install curl for container healthcheck
RUN apk add --no-cache curl

# Create non-root system user & group
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy static assets and standalone server bundle
COPY --from=builder /app/public ./public
COPY --from=builder /app/next.config.js ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Container Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:3000/ || exit 1

# Enforce non-root execution
USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]
