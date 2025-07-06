# Imagen base oficial de Node.js
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

# Crea y establece el directorio de trabajo
WORKDIR /app

# Copia solo los archivos necesarios primero para aprovechar cache
COPY package*.json ./

# Instala dependencias
RUN npm install

# Copia el resto del código fuente
COPY . .

# Puerto en el que corre tu app
EXPOSE 3000

# Comando para ejecutar la app
CMD ["node", "index.js"]
