# /src/__init__.py
import logging
from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from src.config import Config
from src.db.mongo import init_db
from flask_swagger_ui import get_swaggerui_blueprint

logger = logging.getLogger(__name__)

def _initialize_agents_fallback(app):
    """Fallback simplificado para inicialização de agentes"""
    try:
        logger.info("trying_fallback_agents")
        # Método mais direto - tenta importar e criar agentes
        from src.tasks.agents import create_agents
        agents = create_agents()
        app.agents = agents
        logger.info("fallback_agents_success")
    except Exception as e:
        logger.error("fallback_agents_failed", error=str(e))
        # Cria agentes vazios para não quebrar a aplicação
        app.agents = {}

def create_app():
    """Cria e configura a instância da aplicação Flask."""
    
    app = Flask(__name__)
    app.config.from_object(Config)

    from src.utils.observability import setup_logging
    setup_logging()
    
    # --- CONFIGURAÇÃO DE CORS ---
    # Define de quais origens (URLs de frontend) aceitaremos requisições.
    CORS(app, supports_credentials=True)
    
    jwt = JWTManager(app)
    
    with app.app_context():
        # Inicializa DB (faz conexões necessárias)
        init_db(app)
        
         # Inicialização da memória persistente
        try:
            from src.services.memory_manager import init_conversation_states_collection
            from src.db.mongo import get_db
            init_conversation_states_collection(get_db())
            logger.info("conversation_states_initialized")
        except Exception as e:
            logger.error("conversation_states_init_failed", error=str(e))

        # Inicialização do AgentManager
        try:
            from src.services.agent_manager import AgentManager
            agent_manager = AgentManager()
            
            if agent_manager.initialize():
                app.agent_manager = agent_manager
                app.agents = agent_manager.agents
                logger.info("agent_manager_initialized")
            else:
                logger.error("agent_manager_init_failed")
                _initialize_agents_fallback(app)
                
        except Exception as e:
            logger.error("agent_manager_critical_error", error=str(e))
            _initialize_agents_fallback(app)

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