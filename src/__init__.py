# Arquivo: /src/__init__.py

from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from src.config import Config
from src.db.mongo import init_db

def create_app():
    """Cria e configura a instância principal da aplicação Flask."""
    
    app = Flask(__name__)    
    # Carrega as configurações do arquivo config.py
    app.config.from_object(Config)
    
    # Habilita o CORS para permitir que o frontend acesse a API
    CORS(app)
    
    # Inicializa o JWTManager para gerenciar os tokens
    jwt = JWTManager(app)
    
    # Inicializa a conexão com o banco de dados
    with app.app_context():
        init_db(app)

    # Registra os Blueprints
    from src.api.chat.routes import chat_bp
    from src.api.auth.routes import auth_bp
    
    app.register_blueprint(chat_bp, url_prefix='/api/chat')
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    
    # Adiciona uma rota de "health check" para verificar se o servidor está online
    @app.route('/health')
    def health_check():
        return "Servidor Flask está funcionando perfeitamente!"

    return app