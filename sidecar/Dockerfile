# Use Node.js 20 with Debian (slim image for better package availability)
FROM node:20-bullseye-slim

# Install Chromium and minimal dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ca-certificates \
    fonts-liberation \
    libnss3 \
    libxss1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app/sidecar

# Copy package files
COPY package*.json ./

# Copy patches directory (required for patch-package)
COPY patches ./patches

# Install Node dependencies and apply patches
RUN npm install && npm run postinstall

# Copy application code
COPY . .

# Create auth directory
RUN mkdir -p auth_info_baileys

# Expose port (if needed for health checks or API)
EXPOSE 3000 3001

# Run the bot
CMD ["node", "index.js"]
