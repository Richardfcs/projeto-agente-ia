# Arquivo: /src/tasks/agents.py

from crewai import Agent, LLM
from src.tasks.tools import FileReaderTool, TemplateFillerTool, SimpleDocumentGeneratorTool
from src.config import Config

# Instancia as ferramentas para que os agentes possam usá-las.
# É importante instanciá-las apenas uma vez e reutilizá-las.
file_reader_tool = FileReaderTool()
template_filler_tool = TemplateFillerTool()
simple_doc_generator_tool = SimpleDocumentGeneratorTool()

llm = LLM(
    model="gemini/gemini-2.5-flash",
    temperature=0.5
)

# --- DEFINIÇÃO DOS AGENTES DA EQUIPE ---

agente_roteador = Agent(
    role="Gerente de Projetos de IA",
    goal="Analisar solicitações complexas e criar um plano de ação passo a passo, perfeitamente detalhado e acionável, para ser executado por um especialista.",
    backstory=(
        "Você é o estrategista. Sua única saída é um plano de texto. Você quebra problemas em pedaços lógicos. "
        "Sua especialidade é criar prompts e instruções para outros agentes. Você nunca executa o trabalho, apenas o planeja com perfeição."
    ),
    llm=llm,
    tools=[],
    allow_delegation=True,
    verbose=True
)

agente_executor_de_arquivos = Agent(
    role="Especialista em Documentos",
    goal="Executar planos de ação de manipulação de documentos usando as ferramentas fornecidas.",
    backstory=(
        "Você é o executor. Você recebe um plano de ação e o segue à risca. Sua única função é invocar as "
        "ferramentas (`FileReaderTool`, `TemplateFillerTool`) exatamente como instruído."
    ),
    llm=llm,
    tools=[file_reader_tool, template_filler_tool],
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