# WhatsApp sidecar (Baileys). QA-022 / QA-028: reproducible build, no Chromium, supported base image.

# ---- build stage: needs git because one Baileys dependency (libsignal) is a git URL ----
FROM node:24-bookworm-slim AS build
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app/sidecar
COPY package.json package-lock.json ./
# Exactly what the lockfile says (npm ci fails if package.json and the lock disagree).
RUN git config --global url."https://github.com/".insteadOf "ssh://git@github.com/" \
    && npm ci --omit=dev

# ---- runtime stage ----
FROM node:24-bookworm-slim
ENV NODE_ENV=production
WORKDIR /app/sidecar
COPY --from=build /app/sidecar/node_modules ./node_modules
COPY package.json index.js ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# The WhatsApp session lives in the auth_data volume. The entrypoint fixes its ownership and then runs the bot as the
# unprivileged "node" user (QA-025); the image itself never runs the application as root.
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && mkdir -p /app/sidecar/auth_data && chown -R node:node /app/sidecar
EXPOSE 3001
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD node -e "fetch('http://127.0.0.1:'+(process.env.PORT||3001)+'/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["node", "index.js"]
