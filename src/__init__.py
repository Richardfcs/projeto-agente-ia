# Arquivo: /src/__init__.py (VERSÃO SIMPLIFICADA)

from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from src.config import Config
from src.db.mongo import init_db

def create_app():
    """Cria e configura a instância da aplicação Flask."""
    
    app = Flask(__name__)
    app.config.from_object(Config)
    
    CORS(app)
    jwt = JWTManager(app)
    
    with app.app_context():
        init_db(app)

    # Registra os Blueprints
    from src.api.chat.routes import chat_bp
    from src.api.auth.routes import auth_bp
    from src.api.files.routes import files_bp
    
    app.register_blueprint(chat_bp, url_prefix='/api/chat')
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(files_bp, url_prefix='/api')
    
    @app.route('/health')
    def health_check():
        return "Servidor Flask está funcionando perfeitamente!"

    return app