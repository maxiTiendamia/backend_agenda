FROM node:20-slim

# Instalar dependencias del sistema y Google Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg2 \
    ca-certificates \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgdk-pixbuf2.0-0 \
    libnspr4 \
    libnss3 \
    libx11-xcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    xdg-utils \
    libu2f-udev \
    libvulkan1 \
    unzip \
    curl \
  && wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - \
  && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
  && apt-get update && apt-get install -y google-chrome-stable \
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Evitar que puppeteer descargue Chromium
ENV PUPPETEER_SKIP_DOWNLOAD=true

# Crear directorio de trabajo
WORKDIR /app

# Copiar archivos desde el contexto (que ya es venom-service)
COPY ./venom-service/package*.json ./
RUN npm install

# Copiar todo el código
COPY . .

EXPOSE 3000
CMD ["node", "index.js"]
