
FROM node:20-slim

# Instalar solo dependencias mínimas recomendadas por Puppeteer
RUN apt-get update && apt-get install -y \
    wget \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxss1 \
    libgconf-2-4 \
    libu2f-udev \
    libgdk-pixbuf2.0-0 \
    libvulkan1 \
    xdg-utils \
    --no-install-recommends \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Cambiar al directorio correcto
WORKDIR /app

# Copiar solo lo que necesita venom-service
COPY ./package*.json ./
RUN npm install

COPY . ./

# Hacer ejecutables los scripts si existen
RUN chmod +x start.sh debug.sh 2>/dev/null || true

EXPOSE 3000

CMD ["node", "index.js"]