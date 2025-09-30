# /src/tasks/agents.py (refatorado - mantendo LLM nativo do CrewAI)
from typing import Dict
# Mantendo a importação original como solicitado
from crewai import Agent, LLM
from src.config import Config

# NÃO importar tools aqui para evitar ciclos; a importação é feita dentro da fábrica.

def create_agents() -> Dict[str, Agent]:
    """
    Instancia o LLM e os agentes. Use esta função no startup do app (por ex. create_app()).
    Retorna um dict com os agentes para uso pela camada orquestradora.
    """
    # Configurável via Config. O CrewAI suporta o formato "provider/model"
    model_name = getattr(Config, "LLM_MODEL", "gemini/gemini-2.5-flash")
    temp = getattr(Config, "LLM_TEMPERATURE", 0.7)
    debug_verbose = getattr(Config, "DEBUG_VERBOSE_AGENTS", False)

    # Usando a classe nativa LLM do CrewAI, conforme solicitado.
    # Isso funcionará perfeitamente se suas variáveis de ambiente (ex: GOOGLE_API_KEY) estiverem configuradas.
    llm = LLM(model=model_name, temperature=temp)

    # Importar as ferramentas aqui para reduzir risco de import cycle
    from src.tasks.tools import (
        FileReaderTool,
        TemplateFillerTool,
        SimpleDocumentGeneratorTool,
        DatabaseQueryTool,
        TemplateInspectorTool,
        TemplateListerTool,
    )

    # Instanciar tools
    file_reader_tool = FileReaderTool()
    template_filler_tool = TemplateFillerTool()
    template_inspector_tool = TemplateInspectorTool()
    simple_doc_generator_tool = SimpleDocumentGeneratorTool()
    database_query_tool = DatabaseQueryTool()
    template_lister_tool = TemplateListerTool()

    # --- Agentes ---
    
    agente_gerente = Agent(
        role="Gerente de Projetos de IA",
        goal=(
            "Analisar solicitações do usuário e o histórico para orquestrar sua equipe "
            "de especialistas, decompondo a solicitação principal em um plano de subtarefas claro."
        ),
        backstory=(
            "Você é o Gerente. Você planeja e cria um plano de execução. NÃO executa trabalho prático. "
            "Sua função é delegar para o especialista correto."
        ),
        llm=llm,
        tools=[],  # Gerente não acessa ferramentas diretamente; ele delega.
        allow_delegation=False,  # Essencial para a orquestração funcionar
        verbose=debug_verbose,
    )

    agente_analista_de_conteudo = Agent(
        role="Analista de Conteúdo e Estrutura",
        # MELHORIA: Objetivo mais explícito sobre o formato de saída esperado.
        goal="Analisar um pedido e um template para gerar o conteúdo necessário. A saída deve ser um dicionário (JSON) mapeando cada placeholder ao seu conteúdo.",
        backstory="Você é especialista em mapear placeholders para conteúdo. Sua primeira ação é sempre inspecionar o template.",
        llm=llm,
        tools=[template_inspector_tool],
        allow_delegation=False,
        verbose=debug_verbose,
    )

    agente_especialista_documentos = Agent(
        role="Especialista em Documentos",
        goal="Executar tarefas técnicas de criação, leitura e preenchimento de arquivos usando as ferramentas fornecidas.",
        backstory="Você é um executor técnico. Use as tools com os parâmetros exatos que lhe forem passados.",
        llm=llm,
        tools=[
            file_reader_tool,
            template_filler_tool,
            simple_doc_generator_tool,
            database_query_tool,
        ],
        allow_delegation=False,
        verbose=debug_verbose,
    )

    agente_revisor_final = Agent(
        role="Revisor Final e Especialista em Comunicação",
        goal="Analisar o resultado final técnico e formatar a resposta ao usuário de forma amigável.",
        backstory="Você transforma resultados técnicos (como IDs de documentos) em mensagens polidas e úteis para o usuário.",
        llm=llm,
        # MELHORIA: Adicionado database_query_tool, permitindo que ele verifique o resultado final se necessário.
        tools=[template_lister_tool, database_query_tool],
        allow_delegation=False,
        verbose=debug_verbose,
    )
    
    agente_conversador = Agent(
        role="Especialista em Conversação",
        goal="Responder perguntas gerais do usuário.",
        backstory="Assistente amigável.",
        llm=llm,
        tools=[],
        allow_delegation=False,
        verbose=debug_verbose,
    )

    return {
        "gerente": agente_gerente,
        "especialista_documentos": agente_especialista_documentos,
        "analista_de_conteudo": agente_analista_de_conteudo,
        "revisor_final": agente_revisor_final,
        "conversador": agente_conversador,
    }