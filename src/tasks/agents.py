# Arquivo: /src/tasks/agents.py

from crewai import Agent, LLM
from src.tasks.tools import FileReaderTool, TemplateFillerTool, SimpleDocumentGeneratorTool, DatabaseQueryTool
from src.config import Config

# --- CONFIGURAÇÃO DO LLM ---
# Recomenda-se usar um modelo estável e publicamente disponível.
llm = LLM(
    model="gemini/gemini-2.5-flash",
    temperature=0.8
)

# --- INSTÂNCIA DAS FERRAMENTAS ---
# É uma boa prática instanciar todas as ferramentas aqui.
file_reader_tool = FileReaderTool()
template_filler_tool = TemplateFillerTool()
simple_doc_generator_tool = SimpleDocumentGeneratorTool()
database_query_tool = DatabaseQueryTool()


# --- DEFINIÇÃO DOS AGENTES DA EQUIPE ---

agente_roteador = Agent(
    role="Gerente de Projetos de IA",
    goal="Analisar solicitações complexas e criar um plano de ação passo a passo, perfeitamente detalhado e acionável, para ser executado por um especialista.",
    backstory=(
        "Você é o estrategista. Sua única saída é um plano de texto. Você quebra problemas em pedaços lógicos. "
        "Sua especialidade é criar prompts e instruções para outros agentes. Você nunca executa o trabalho, apenas o planeja com perfeição."
    ),
    llm=llm,
    tools=[], # O gerente planeja, não executa ferramentas.
    allow_delegation=True, # Essencial para que ele possa delegar tarefas.
    verbose=True
)

agente_executor_de_arquivos = Agent(
    role="Especialista em Documentos",
    goal="Executar planos de ação de manipulação de documentos usando as ferramentas fornecidas.",
    backstory=(
        "Você é o executor. Você recebe um plano de ação e o segue à risca. Sua única função é invocar as "
        "ferramentas (`FileReaderTool`, `TemplateFillerTool`, `SimpleDocumentGeneratorTool`, etc.) exatamente como instruído."
    ),
    llm=llm,
    # A lista de ferramentas agora está completa com todas as nossas capacidades.
    tools=[
        file_reader_tool,
        template_filler_tool,
        simple_doc_generator_tool,
        database_query_tool
    ],
    allow_delegation=False,
    verbose=True
)

agente_conversador = Agent(
    role="Especialista em Conversação",
    goal="Responder diretamente a perguntas gerais do usuário.",
    backstory="Você é um assistente de IA amigável. Você não usa ferramentas, apenas conversa.",
    llm=llm,
    tools=[],
    allow_delegation=False,
    verbose=True
)