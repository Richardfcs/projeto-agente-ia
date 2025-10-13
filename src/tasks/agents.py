# /src/tasks/agents.py (refatorado - mantendo LLM nativo do CrewAI)
from typing import Dict
from crewai import Agent, LLM
from src.config import Config

def create_agents() -> Dict[str, Agent]:
    """
    Instancia o LLM e os agentes. Use esta função no startup do app.
    Retorna um dict com os agentes para uso pela camada orquestradora.
    """
    # Configurável via Config. O CrewAI suporta o formato "provider/model"
    model_name = getattr(Config, "LLM_MODEL", "gemini/gemini-2.5-flash-lite")
    temp = getattr(Config, "LLM_TEMPERATURE", 0.7)
    debug_verbose = getattr(Config, "DEBUG_VERBOSE_AGENTS", False)

    # Usando a classe nativa LLM do CrewAI, conforme solicitado.
    # Isso funcionará perfeitamente se suas variáveis de ambiente (ex: GOOGLE_API_KEY) estiverem configuradas.
    llm = LLM(model=model_name, temperature=temp)

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
            "Analisar solicitações do usuário para classificar a intenção principal e delegar "
            "a tarefa para o especialista apropriado. Sua principal função é rotear a solicitação."
        ),
        backstory=(
            "Você é o cérebro da operação. Você não executa tarefas práticas, mas entende "
            "profundamente o que o usuário quer e qual agente é o melhor para o trabalho. "
            "Sua análise inicial é crucial para a eficiência de toda a equipe."
        ),
        llm=llm, # Opcional se configurado globalmente no Crew
        tools=[],
        allow_delegation=True,
        verbose=debug_verbose,
    )

    agente_analista_de_conteudo = Agent(
        role="Analista de Conteúdo e Estrutura de Dados",
        goal="Analisar um pedido e um template para gerar o conteúdo necessário em um formato JSON estruturado e preciso.",
        backstory=(
            "Você é um especialista em extrair informações de conversas e estruturá-las perfeitamente em JSON. "
            "Sua primeira ação é sempre usar a ferramenta de inspeção de templates para saber exatamente qual "
            "estrutura de dados você precisa criar. Você é meticuloso e nunca erra a sintaxe do JSON."
        ),
        llm=llm,
        tools=[template_inspector_tool],
        allow_delegation=False,
        verbose=debug_verbose,
    )

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
        llm=llm,
        allow_delegation=False,
        verbose=debug_verbose,
    )

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
        llm=llm,
        tools=[template_lister_tool, database_query_tool],
        allow_delegation=False,
        verbose=debug_verbose,
    )
        
    # O agente_conversador agora se torna um 'alias' para o revisor_final,
    # garantindo que a mesma persona inteligente lide com todas as conversas gerais.
    agente_conversador = agente_revisor_final
    # --- FIM DA MUDANÇA ---

    return {
        "gerente": agente_gerente,
        "especialista_documentos": agente_especialista_documentos,
        "analista_de_conteudo": agente_analista_de_conteudo,
        "revisor_final": agente_revisor_final,
        "conversador": agente_conversador,
    }