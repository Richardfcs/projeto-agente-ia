# src/services/agent_manager.py

import threading
from typing import Dict, Any, Optional
from crewai import Agent, LLM
from src.config import Config
from src.utils.observability import log_with_context, track_performance

logger = log_with_context(component="AgentManager")

class AgentManager:
    """
    Singleton para gerenciar os agentes da aplicação.
    Garante que os agentes sejam criados apenas uma vez durante o ciclo de vida da aplicação.
    """
    
    _instance = None
    _lock = threading.Lock()
    _initialized = False
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        """Inicialização uma única vez"""
        if self._initialized:
            return
            
        self._llm: Optional[LLM] = None
        self._agents: Dict[str, Agent] = {}
        self._initialized = True
        logger.info("AgentManager instanciado")
        
    @track_performance
    def initialize(self) -> bool:
        """
        Inicializa o LLM e cria os agentes.
        Retorna True se bem-sucedido, False caso contrário.
        """
        try:
            with self._lock:
                if self._llm is not None:
                    logger.info("AgentManager já inicializado")
                    return True
                
                logger.info("Inicializando AgentManager...")
                
                # Configuração do LLM
                model_name = getattr(Config, "LLM_MODEL", "gemini/gemini-2.5-flash")
                temperature = getattr(Config, "LLM_TEMPERATURE", 0.7)
                debug_verbose = getattr(Config, "DEBUG_VERBOSE_AGENTS", False)
                
                logger.info(f"Configurando LLM: {model_name}, temp: {temperature}")
                
                # Cria instância do LLM
                self._llm = LLM(model=model_name, temperature=temperature)
                
                # Cria os agentes
                self._create_agents(debug_verbose)
                
                logger.info("AgentManager inicializado com sucesso")
                return True
                
        except Exception as e:
            logger.error(f"Erro ao inicializar AgentManager: {e}")
            self._llm = None
            self._agents = {}
            return False
    
    def _create_agents(self, debug_verbose: bool):
        """Cria todos os agentes usando o LLM compartilhado"""
        from src.tasks.tools import (
            FileReaderTool,
            TemplateFillerTool,
            SimpleDocumentGeneratorTool,
            DatabaseQueryTool,
            TemplateInspectorTool,
            TemplateListerTool,
        )
        
        logger.info("Criando agentes...")
        
        # Instanciar tools (cada tool é stateless, pode ser reutilizada)
        file_reader_tool = FileReaderTool()
        template_filler_tool = TemplateFillerTool()
        template_inspector_tool = TemplateInspectorTool()
        simple_doc_generator_tool = SimpleDocumentGeneratorTool()
        database_query_tool = DatabaseQueryTool()
        template_lister_tool = TemplateListerTool()
        
        # --- Agente Gerente ---
        agente_gerente = Agent(
            role="Gerente de Projetos de IA",
            goal=(
                "Analisar solicitações do usuário para classificar a intenção principal e delegar "
                "a tarefa para o especialista apropriado. Sua principal função é rotear a solicitação."
            ),
            backstory=(
                "Você é o cérebro da operação. Você não executa tarefas práticas, mas entende "
                "profundamente o que o usuário quer e qual agente é o melhor para o trabalho. "
                "Sua análise inicial é crucial para a eficiência de toda a equipe."
            ),
            llm=self._llm,
            tools=[],
            allow_delegation=True,
            verbose=debug_verbose,
        )
        
        # --- Agente Analista de Conteúdo ---
        agente_analista_de_conteudo = Agent(
            role="Analista de Conteúdo e Estrutura de Dados",
            goal="Analisar um pedido e um template para gerar o conteúdo necessário em um formato JSON estruturado e preciso.",
            backstory=(
                "Você é um especialista em extrair informações de conversas e estruturá-las perfeitamente em JSON. "
                "Sua primeira ação é sempre usar a ferramenta de inspeção de templates para saber exatamente qual "
                "estrutura de dados você precisa criar. Você é meticuloso e nunca erra a sintaxe do JSON."
            ),
            llm=self._llm,
            tools=[template_inspector_tool],
            allow_delegation=False,
            verbose=debug_verbose,
        )
        
        # --- Agente Especialista Documentos ---
        agente_especialista_documentos = Agent(
            role="Especialista Técnico em Documentos",
            goal="Executar tarefas técnicas de criação, leitura e preenchimento de arquivos usando as ferramentas fornecidas, com base em instruções exatas.",
            backstory=(
                "Você é um executor técnico focado. Você recebe instruções claras e usa suas ferramentas para executar a tarefa. "
                "**PROTOCOLO DE RECUPERAÇÃO DE FALHAS:** Se você usar uma ferramenta e receber uma mensagem de erro (ex: 'Template não encontrado'), você NÃO deve parar. "
                "Seu protocolo é: "
                "1. **Analise a mensagem de erro** para entender a causa (ex: nome do template errado). "
                "2. **Releia a última instrução do usuário** no histórico da conversa para encontrar o parâmetro correto. "
                "3. **Tente executar a ferramenta novamente**, desta vez com os parâmetros corrigidos. "
                "Se a falha persistir, passe o erro detalhado para a próxima etapa para que o revisor possa ajudar o usuário."
            ),
            tools=[
                file_reader_tool,
                template_filler_tool,
                simple_doc_generator_tool,
                database_query_tool,
            ],
            llm=self._llm,
            allow_delegation=False,
            verbose=debug_verbose,
        )
        
        # --- Agente Revisor Final ---
        agente_revisor_final = Agent(
            role="Assistente de IA Multifuncional de Comunicação",
            goal="Ser a interface final com o usuário, traduzindo resultados técnicos, lidando com erros de forma útil e gerenciando conversas gerais de forma inteligente e criativa.",
            backstory=(
                "Você é um assistente de IA avançado e amigável com DOIS MODOS DE OPERAÇÃO:\n\n"
                "1. **MODO TRADUTOR TÉCNICO:** Quando o resultado de uma tarefa anterior é um JSON (de sucesso ou erro), sua função é TRADUZIR. "
                "Se for um JSON de sucesso, crie uma mensagem amigável como 'Seu documento foi criado!'. "
                "Se for um JSON de erro como 'Template não encontrado', sua função é AJUDAR. Use a ferramenta `TemplateListerTool` e sugira os templates corretos para o usuário. NUNCA apenas repita o erro técnico.\n\n"
                "2. **MODO CONVERSACIONAL:** Quando a tarefa é de 'CONVERSA GERAL', sua função é SER O ASSISTENTE. Você DEVE atender a pedidos criativos (piadas, poemas, resumos de texto) e responder a perguntas. "
                "**NUNCA SE RECUSE A ATENDER UM PEDIDO NESTE MODO.** Sua flexibilidade é sua maior qualidade."
            ),
            llm=self._llm,
            tools=[template_lister_tool, database_query_tool],
            allow_delegation=False,
            verbose=debug_verbose,
        )
        
        # O agente_conversador é um alias para o revisor_final
        agente_conversador = agente_revisor_final
        
        # Armazena os agentes
        self._agents = {
            "gerente": agente_gerente,
            "especialista_documentos": agente_especialista_documentos,
            "analista_de_conteudo": agente_analista_de_conteudo,
            "revisor_final": agente_revisor_final,
            "conversador": agente_conversador,
        }
        
        logger.info(f"Agentes criados: {list(self._agents.keys())}")
    
    @property
    def agents(self) -> Dict[str, Agent]:
        """Retorna o dicionário de agentes"""
        if not self._agents:
            raise RuntimeError("AgentManager não foi inicializado. Chame initialize() primeiro.")
        return self._agents
    
    def get_agent(self, agent_name: str) -> Agent:
        """Retorna um agente específico pelo nome"""
        if agent_name not in self._agents:
            available = list(self._agents.keys())
            raise KeyError(f"Agente '{agent_name}' não encontrado. Agentes disponíveis: {available}")
        return self._agents[agent_name]
    
    def is_initialized(self) -> bool:
        """Verifica se o AgentManager foi inicializado"""
        return self._llm is not None and bool(self._agents)
    
    def reload_agents(self) -> bool:
        """Recria os agentes (útil se a configuração mudar)"""
        logger.info("Recarregando agentes...")
        self._agents = {}
        return self.initialize()