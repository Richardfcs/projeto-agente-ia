# Arquivo: /src/tasks/agents.py

from crewai import Agent, LLM
from src.tasks.tools import FileReaderTool, TemplateFillerTool
from src.config import Config

# Instancia as ferramentas para que os agentes possam usá-las.
# É importante instanciá-las apenas uma vez e reutilizá-las.
file_reader_tool = FileReaderTool()
template_filler_tool = TemplateFillerTool()

llm = LLM(
    model="gemini/gemini-2.5-flash-lite",
    temperature=0.5
)

# --- DEFINIÇÃO DOS AGENTES DA EQUIPE ---

agente_roteador = Agent(
    role="Analista e Roteador de Tarefas Sênior",
    goal=(
        "Analisar de forma rigorosa o histórico de uma conversa e o último pedido do usuário. "
        "Sua principal responsabilidade é traduzir esse pedido em uma instrução perfeitamente clara e "
        "acionável para ser delegada a um agente especialista."
    ),
    backstory=(
        "Você é o cérebro da operação, um mestre em interpretar a intenção humana. "
        "Você não executa tarefas, você as planeja. Você lê o que o usuário quer e "
        "cria um plano de ação detalhado. Se o usuário quer ler um arquivo, sua instrução "
        "deve ser 'Use a ferramenta Leitor de Arquivos...'. Se ele quer usar um template, "
        "sua instrução deve ser 'Use a ferramenta Preenchedor de Templates...'. "
        "Sua saída é a entrada para o próximo agente."
    ),
    llm=llm,
    allow_delegation=True,
    verbose=True
)

agente_executor_de_arquivos = Agent(
    role="Especialista em Documentos e Ferramentas",
    goal="Executar com precisão as tarefas de manipulação de documentos que lhe são delegadas.",
    backstory=(
        "Você é um especialista prático. Você recebe uma instrução clara do Analista e a executa "
        "usando uma de suas ferramentas. Você não toma decisões, apenas segue o plano. "
        "Suas ferramentas são para ler arquivos e preencher templates. Sua resposta final "
        "é sempre o resultado direto da ferramenta que você usou."
    ),
    llm=llm,
    tools=[file_reader_tool, template_filler_tool],
    allow_delegation=False,
    verbose=True
)

agente_conversador = Agent(
    role="Especialista em Conversação",
    goal="Responder diretamente a perguntas gerais do usuário de forma clara e concisa.",
    backstory=(
        "Você é um assistente de IA amigável e prestativo. Sua especialidade é manter uma conversa fluida. "
        "Você não usa ferramentas; apenas usa seu conhecimento para responder perguntas sobre tecnologia, "
        "o funcionamento do sistema ou qualquer outro tópico geral."
    ),
    llm=llm,
    tools=[],
    allow_delegation=False,
    verbose=True
)