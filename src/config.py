# Arquivo: /src/config.py

import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    """Classe de configuração simplificada sem Celery."""
    SECRET_KEY = os.environ.get('SECRET_KEY')
    MONGO_URI = os.environ.get('MONGO_URI')
    MONGO_DB_NAME = os.environ.get('MONGO_DB_NAME')
    GOOGLE_API_KEY = os.environ.get('GOOGLE_API_KEY')