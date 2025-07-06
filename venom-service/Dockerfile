# Imagen base de Node.js
FROM node:20-slim

# Instala dependencias necesarias para Puppeteer/Venom
RUN apt-get update && apt-get install -y \
  wget \
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
  && apt-get clean && rm -rf /var/lib/apt/lists/*

# Directorio de trabajo
WORKDIR /app

# Copia los archivos desde venom-service/
COPY ./venom-service/package*.json ./
RUN npm install

COPY ./venom-service ./

# Puerto (ajustalo si usás otro en index.js)
EXPOSE 3000

# Comando para iniciar tu app
CMD ["node", "index.js"]
