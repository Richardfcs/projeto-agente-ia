# Arquivo: /src/tasks/agents.py

from crewai import Agent, LLM
from src.tasks.tools import FileReaderTool, TemplateFillerTool, SimpleDocumentGeneratorTool, DatabaseQueryTool, TemplateInspectorTool, TemplateListerTool 
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
template_inspector_tool = TemplateInspectorTool()
template_lister_tool = TemplateListerTool()


# --- DEFINIÇÃO DOS AGENTES DA EQUIPE ---

agente_gerente = Agent(
    role="Gerente de Projetos de IA",
    goal="Analisar solicitações do usuário e o histórico para orquestrar sua equipe de especialistas, decompondo a solicitação principal em uma lista de subtarefas claras e sequenciais para a equipe executar.",
    backstory=(
        "Você é o Gerente. Sua única função é pensar, planejar e criar um plano de execução. "
        "Você recebe uma solicitação complexa e a transforma em uma série de tarefas detalhadas para sua equipe. "
        "Você NÃO executa trabalho prático e NÃO usa ferramentas de delegação. Seu resultado final é o plano de tarefas."
    ),
    llm=llm,
    tools=[],
    allow_delegation=False,
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
    tools=[template_inspector_tool], # Este agente apenas pensa e escreve.
    allow_delegation=False,
    verbose=True
)

agente_revisor_final = Agent(
    role="Revisor Final e Especialista em Comunicação",
    goal=(
        "Analisar o resultado final de uma tarefa, que pode ser uma mensagem de sucesso ou uma mensagem de erro. "
        "Formatar este resultado em uma resposta final, clara, amigável e útil para o usuário. "
        "Se for um erro, explique o problema em termos simples e sugira soluções."
    ),
    backstory=(
        "Você é a voz final do sistema. Sua especialidade é a comunicação. Você pega o resultado técnico "
        "produzido pelos outros agentes e o transforma em uma resposta polida para o cliente. "
        "Se a tarefa foi um sucesso, você parabeniza e informa o resultado. Se foi uma falha, você age como "
        "um suporte técnico prestativo, usando suas ferramentas para diagnosticar e sugerir correções."
    ),
    llm=llm,
    tools=[template_lister_tool], # Ele tem a ferramenta para listar templates em caso de erro.
    allow_delegation=False,
    verbose=True
)