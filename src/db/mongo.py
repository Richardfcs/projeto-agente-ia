# Arquivo: src/db/mongo.py

from pymongo import MongoClient
from gridfs import GridFS

# Variáveis globais para o cliente e o banco de dados
mongo_client = None
db = None
fs = None

def init_db(app):
    """Inicializa as conexões com MongoDB e GridFS."""
    global mongo_client, db, fs
    
    mongo_client = MongoClient(app.config['MONGO_URI'])
    db = mongo_client.get_default_database() # O nome do DB vem da URI
    fs = GridFS(db)
    
    try:
        mongo_client.admin.command('ping')
        print("Conexão com MongoDB estabelecida com sucesso!")
    except Exception as e:
        print(f"Erro ao conectar com o MongoDB: {e}")

def get_db():
    """Retorna a instância do banco de dados."""
    return db

def get_gridfs():
    """Retorna a instância do GridFS."""
    return fs