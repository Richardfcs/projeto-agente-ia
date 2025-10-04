# /src/__init__.py
import logging
from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from src.config import Config
from src.db.mongo import init_db
from flask_swagger_ui import get_swaggerui_blueprint

logger = logging.getLogger(__name__)

def create_app():
    """Cria e configura a instância da aplicação Flask."""
    
    app = Flask(__name__)
    app.config.from_object(Config)
    
    # --- CONFIGURAÇÃO DE CORS ---
    # Define de quais origens (URLs de frontend) aceitaremos requisições.
    origins = [
        "http://localhost:3000",
        "https://agente-ia-squad42.onrender.com"
    ]
    
    # Aplica a configuração do CORS à aplicação inteira.
    # `supports_credentials=True` é essencial para permitir que o frontend
    # envie headers de autenticação (como nosso token JWT).
    CORS(app, origins=origins, supports_credentials=True)
    
    jwt = JWTManager(app)
    
    with app.app_context():
        # Inicializa DB (faz conexões necessárias)
        init_db(app)

        # Tenta criar os agentes dinamicamente (fábrica em src.tasks.agents.create_agents)
        # Se a função não existir ou falhar, logamos e seguimos — isso evita quebrar o startup por import-time side-effects.
        try:
            from src.tasks.agents import create_agents
            agents = create_agents()
            # Armazena os agentes na app.extensions (padrão Flask para extensões/objetos)
            app.extensions = getattr(app, "extensions", {})
            app.extensions["agents"] = agents
            # opcional: também expõe como app.agents para conveniência
            app.agents = agents
            logger.info("Agentes instanciados e vinculados à app.extensions['agents']")
        except Exception as e:
            # Se ocorrer erro, não interrompa a inicialização — registre para diagnóstico.
            logger.exception("Falha ao instancenciar agentes via create_agents(): %s", e)
            # Nota: se os blueprints/handlers dependem dos agentes, eles devem falhar de forma controlada quando tentarem usar app.agents.

    # URL onde a especificação (o arquivo .yaml) estará disponível
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

    # Registra os Blueprints da API
    # IMPORTANTE: importe os blueprints aqui (após init_db e tentativa de criar agentes)
    # Isso reduz a chance de import-time cycles causarem inicialização falha.
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