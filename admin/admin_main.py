from flask import Flask
from app.database import init_db, db
from app.admin import init_admin
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'admin-secret')
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['BASIC_AUTH_USERNAME'] = os.getenv("ADMIN_USER")
app.config['BASIC_AUTH_PASSWORD'] = os.getenv("ADMIN_PASSWORD")

init_db(app)
init_admin(app, db)

@app.route("/")
def index():
    return "✅ Panel admin funcionando"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))