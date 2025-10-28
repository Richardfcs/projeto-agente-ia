# src/__init__.py

"""
Ponto de Entrada e Fábrica da Aplicação Flask.

Responsável por criar e configurar a instância da aplicação Flask.
Após a refatoração para LangGraph, a inicialização foi significativamente simplificada.

- A inicialização complexa do 'AgentManager' foi removida.
- O LLM e as ferramentas agora são inicializados de forma 'lazy' (preguiçosa) dentro
  do módulo do grafo (`src/tasks/graph/nodes.py`) quando são realmente necessários.
- A inicialização do banco de dados e dos blueprints da API permanece como a base sólida da aplicação.
"""

import logging
from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_swagger_ui import get_swaggerui_blueprint

from src.config import Config
from src.db.mongo import init_db
from src.utils.observability import setup_logging

logger = logging.getLogger(__name__)

def create_app():
    """Cria e configura a instância da aplicação Flask."""
    
    app = Flask(__name__)
    app.config.from_object(Config)

    # Configura o logging estruturado para toda a aplicação.
    setup_logging()
    
    # Configura o CORS para permitir requisições do frontend.
    CORS(app, supports_credentials=True)
    
    # Inicializa o gerenciador de JWT para autenticação.
    jwt = JWTManager(app)
    
    # O contexto da aplicação é usado para garantir que as conexões
    # e configurações estejam disponíveis quando necessário.
    with app.app_context():
        # Inicializa a conexão com o banco de dados MongoDB e GridFS.
        init_db(app)
        
        # --- LÓGICA DE INICIALIZAÇÃO REMOVIDA ---
        # A inicialização do AgentManager, do memory_manager e dos agentes do CrewAI
        # foi completamente removida daqui. Nossa nova arquitetura com LangGraph não
        # requer um pré-carregamento complexo no startup da aplicação.
        # O grafo e seus componentes são importados e utilizados diretamente pelo
        # orquestrador `ia_processor`.

    # --- Configuração do Swagger UI para a Documentação da API ---
    SWAGGER_URL = '/api/docs'  # URL para a UI do Swagger
    API_URL = '/static/openapi.yaml'  # URL para o arquivo de especificação da API

    swaggerui_blueprint = get_swaggerui_blueprint(
        SWAGGER_URL,
        API_URL,
        config={
            'app_name': "Agente de IA - API Docs"
        }
    )
    app.register_blueprint(swaggerui_blueprint)

    # --- Registro dos Blueprints da API ---
    # Importa e registra as rotas para as diferentes partes da nossa API.
    from src.api.chat.routes import chat_bp
    from src.api.auth.routes import auth_bp
    from src.api.files.routes import files_bp
    
    app.register_blueprint(chat_bp, url_prefix='/api/chat')
    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(files_bp, url_prefix='/api')
    
    @app.route('/health')
    def health_check():
        """Endpoint simples para verificar se a aplicação está no ar."""
        return "Servidor Flask está funcionando perfeitamente!"

    logger.info("Aplicação Flask criada e configurada com sucesso.")
    return app