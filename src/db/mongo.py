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
    
    # MUDANÇA AQUI: Em vez de adivinhar, pegamos o nome do DB explicitamente
    db_name = app.config['MONGO_DB_NAME']
    if not db_name:
        raise ValueError("A variável de ambiente MONGO_DB_NAME não foi definida.")
        
    db = mongo_client[db_name] # Acessa o banco de dados pelo nome
    fs = GridFS(db)
    
    try:
        mongo_client.admin.command('ping')
        print(f"Conexão com MongoDB (DB: {db_name}) estabelecida com sucesso!")
    except Exception as e:
        print(f"Erro ao conectar com o MongoDB: {e}")

def get_db():
    """Retorna a instância do banco de dados."""
    return db

def get_gridfs():
    """Retorna a instância do GridFS."""
    return fs