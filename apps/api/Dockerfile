FROM oven/bun:1-alpine AS base
WORKDIR /app

# Copy workspace root config
COPY package.json bun.lockb* ./
COPY tsconfig.base.json ./

# Copy all packages (workspace deps)
COPY packages/ ./packages/
COPY apps/api/  ./apps/api/

# Install all workspace dependencies
RUN bun install --frozen-lockfile 2>/dev/null || bun install

EXPOSE 8090

CMD ["bun", "run", "apps/api/src/index.ts"]
