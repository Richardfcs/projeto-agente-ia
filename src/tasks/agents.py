# Arquivo: /src/tasks/agents.py

from crewai import Agent, LLM
from src.tasks.tools import FileReaderTool, TemplateFillerTool, SimpleDocumentGeneratorTool, DatabaseQueryTool, TemplateInspectorTool
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
TemplateInspectorTool = TemplateInspectorTool()


# --- DEFINIÇÃO DOS AGENTES DA EQUIPE ---

agente_gerente = Agent(
    role="Gerente de Projetos de IA",
    goal="Analisar solicitações do usuário e o histórico para orquestrar sua equipe de especialistas, delegando tarefas de forma clara e sequencial para atingir o objetivo final.",
    backstory=(
        "Você é o Gerente. Sua única função é pensar, planejar e delegar. Você recebe uma solicitação complexa "
        "e a quebra em subtarefas lógicas para sua equipe. Você não executa trabalho prático. "
        "Sua principal ferramenta é a 'Delegate work to coworker'. Você deve fornecer TODO o contexto necessário "
        "em cada delegação, pois seus especialistas não têm acesso ao histórico completo."
    ),
    llm=llm,
    tools=[],
    allow_delegation=True,
    verbose=True
)

agente_especialista_documentos = Agent(
    role="Especialista em Documentos",
    goal="Executar tarefas específicas de manipulação de arquivos usando as ferramentas fornecidas.",
    backstory=(
        "Você é um especialista focado. Você recebe uma tarefa clara do seu Gerente e a executa. "
        "Sua função é usar as ferramentas (`FileReaderTool`, `TemplateFillerTool`, etc.) "
        "com os parâmetros exatos que lhe foram fornecidos."
    ),
    llm=llm,
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
    backstory="Você é um assistente de IA amigável. Você recebe uma pergunta do seu Gerente e a responde da melhor forma possível.",
    llm=llm,
    tools=[],
    allow_delegation=False,
    verbose=True
)

agente_analista_de_conteudo = Agent(
    role="Analista de Conteúdo e Estrutura",
    goal="Com base em uma lista de placeholders de um template e no pedido do usuário, gerar o conteúdo para cada placeholder, estruturando-o em um dicionário JSON (contexto).",
    backstory=(
        "Você é um especialista em mapeamento de dados. Você recebe uma 'lista de compras' (os placeholders) "
        "e uma 'conversa' (o prompt do usuário). Sua única tarefa é gerar o conteúdo para cada item da lista "
        "e devolver tudo em um único JSON pronto para ser usado pela ferramenta de preenchimento."
    ),
    llm=llm,
    tools=[TemplateInspectorTool], # Este agente apenas pensa e escreve.
    verbose=True
)