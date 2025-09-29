# Arquivo: /src/__init__.py (VERSÃO SIMPLIFICADA)

from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from src.config import Config
from src.db.mongo import init_db
from flask_swagger_ui import get_swaggerui_blueprint

def create_app():
    """Cria e configura a instância da aplicação Flask."""
    
    app = Flask(__name__)
    app.config.from_object(Config)
    
    CORS(app)
    jwt = JWTManager(app)
    
    with app.app_context():
        init_db(app)

    # URL onde a sua especificação (o arquivo .yaml) estará disponível
    SWAGGER_URL = '/api/docs'
    API_URL = '/static/openapi.yaml'

    # Cria o Blueprint da Swagger UI
    swaggerui_blueprint = get_swaggerui_blueprint(
        SWAGGER_URL,
        API_URL,
        config={
            'app_name': "Agente de IA - API Docs"
        }
    )

    # Registra o Blueprint da Swagger
    app.register_blueprint(swaggerui_blueprint)

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