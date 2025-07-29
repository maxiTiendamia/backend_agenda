// Archivo principal de arranque para WebConnect
require('dotenv').config();
const express = require('express');
const app = express();
const PORT = process.env.PORT || 3000;

app.use(express.json());

const webconnectRoutes = require('./routes/webconnectRoutes');
app.use('/', webconnectRoutes);

app.get('/health', (req, res) => {
  res.send('Service is running');
});

app.listen(PORT, () => {
  console.log(`Server is running on port ${PORT}`);
});