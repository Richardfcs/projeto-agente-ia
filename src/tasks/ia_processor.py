# /src/tasks/ia_processor.py (versão final, completa e robusta)

import json
import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from bson import ObjectId
from crewai import Crew, Process, Task, Agent
from src.db.mongo import get_db

logger = logging.getLogger(__name__)

# --- Funções de Suporte ---

# Carregador Resiliente de Agentes
# Garante que os agentes estejam disponíveis, seja em um contexto Flask ou não.
try:
    from src.tasks.agents import create_agents
except ImportError:
    create_agents = None

_module_agents: Dict[str, Any] = {}

def _ensure_agents() -> Dict[str, Any]:
    """
    Garante que exista um dicionário com agentes disponível.
    1) Tenta obter de current_app.agents (quando em contexto Flask).
    2) Se não houver contexto, usa um cache local se já preenchido.
    3) Como fallback, cria os agentes usando a fábrica create_agents().
    """
    global _module_agents
    try:
        from flask import current_app
        app_agents = getattr(current_app, "agents", current_app.extensions.get("agents"))
        if app_agents:
            _module_agents = app_agents
            return _module_agents
    except (RuntimeError, AttributeError):
        # Ignora se não houver contexto de aplicação Flask.
        pass

    if _module_agents:
        return _module_agents

    if create_agents is None:
        raise RuntimeError("Fábrica de agentes create_agents() não pôde ser importada.")

    try:
        _module_agents = create_agents()
        return _module_agents
    except Exception as e:
        logger.exception("Falha ao criar agentes dinamicamente via create_agents(): %s", e)
        raise RuntimeError(f"Erro ao instanciar agentes via create_agents(): {e}") from e

# Agente Classificador de Intenção
def _classificar_intencao(historico_texto: str) -> str:
    """
    Usa um agente simples para classificar a intenção do usuário em uma categoria predefinida.
    Esta é a etapa de "roteamento" que adiciona flexibilidade ao sistema.
    """
    agents = _ensure_agents()
    gerente = agents["gerente"] # Usamos a inteligência do gerente para classificar

    task = Task(
        description=(
            "Analise o histórico da conversa, focando na última mensagem do usuário, para classificar a intenção principal. "
            "Responda APENAS com uma das seguintes categorias, sem nenhuma outra palavra ou pontuação:\n"
            "- 'PREENCHER_TEMPLATE'\n"
            "- 'CRIAR_DOCUMENTO_SIMPLES'\n"
            "- 'LER_DOCUMENTO'\n"
            "- 'CONVERSA_GERAL'\n\n"
            "Se o usuário menciona um nome de arquivo de template (ex: 'TEMPLATE_TPF.docx'), a intenção é 'PREENCHER_TEMPLATE'.\n"
            "Se ele pede um relatório ou documento mas não menciona um template, é 'CRIAR_DOCUMENTO_SIMPLES'.\n"
            "Se ele faz uma pergunta sobre um documento anexado, é 'LER_DOCUMENTO'.\n"
            "Para qualquer outra coisa (saudações, perguntas genéricas), é 'CONVERSA_GERAL'.\n\n"
            f"--- HISTÓRICO ---\n{historico_texto}"
        ),
        expected_output="Uma única string de categoria: PREENCHER_TEMPLATE, CRIAR_DOCUMENTO_SIMPLES, LER_DOCUMENTO, ou CONVERSA_GERAL.",
        agent=gerente,
    )
    
    crew = Crew(agents=[gerente], tasks=[task], verbose=0)
    resultado = crew.kickoff()
    
    # Converte o objeto de resultado 'CrewOutput' para uma string antes de chamar .strip()
    return str(resultado).strip()

# Extrator de Nome de Template
def _extrair_nome_template(texto: str) -> Optional[str]:
    """
    Usa regex para encontrar um nome de arquivo de template no texto.
    Procura por padrões como 'TEMPLATE_TPF.docx' entre aspas.
    """
    match = re.search(r"\'([\w\d_-]+\.docx?)\'|\"([\w\d_-]+\.docx?)\"", texto, re.IGNORECASE)
    if match:
        # O resultado pode estar no grupo 1 (aspas simples) ou 2 (aspas duplas)
        return match.group(1) or match.group(2)
    return None

def _extrair_extensao_desejada(texto: str) -> str:
    """Detecta a extensão de arquivo solicitada no prompt e retorna 'docx', 'xlsx', ou 'pdf'."""
    texto_lower = texto.lower()
    if 'xlsx' in texto_lower or 'planilha' in texto_lower or 'excel' in texto_lower:
        return 'xlsx'
    if 'pdf' in texto_lower:
        return 'pdf'
    # DOCX é o padrão se nada for especificado
    return 'docx'

# --- Orquestrador Principal ---
def processar_solicitacao_ia(message_id: str) -> str:
    """
    Orquestra a equipe de IA classificando a intenção do usuário primeiro e
    depois construindo um pipeline de tarefas sequencial e explícito.
    """
    logger.info("Iniciando orquestração com roteamento de intenção para a mensagem: %s", message_id)
    db = get_db()
    
    mensagem_atual = None
    try:
        # Garante que temos as instâncias dos agentes
        agents = _ensure_agents()
        analista = agents["analista_de_conteudo"]
        especialista_doc = agents["especialista_documentos"]
        revisor = agents["revisor_final"]

        # Obtém todo o contexto da conversa do banco de dados
        mensagem_atual = db.messages.find_one({"_id": ObjectId(message_id)})
        conversation_id = mensagem_atual["conversation_id"]
        user_id = str(mensagem_atual["user_id"])
        historico_cursor = db.messages.find({"conversation_id": conversation_id}).sort("timestamp", 1)
        historico_texto = "\n".join([f"{msg.get('role')}: {msg.get('content')}" for msg in historico_cursor])

        # Passo 1: Usa o agente classificador para entender o que o usuário quer
        intencao = _classificar_intencao(historico_texto)
        logger.info(f"Intenção detectada: {intencao}")

        # Passo 2: Monta dinamicamente a lista de tarefas e agentes para a Crew
        tasks: List[Task] = []
        crew_agents: List[Agent] = []

        if intencao == 'PREENCHER_TEMPLATE':
            template_name = _extrair_nome_template(historico_texto)
            if not template_name:
                intencao = 'CONVERSA_GERAL'
                historico_texto += "\n\nsystem: A intenção parece ser preencher um template, mas o nome do arquivo não foi encontrado. Peça ao usuário para especificar o nome do template."
            else:
                logger.info(f"Template detectado: {template_name}")
                
                # --- MUDANÇA CRÍTICA: PROMPT MUITO MAIS DETALHADO E INTELIGENTE ---
                tarefa_analise = Task(
                    description=(
                        f"Sua missão é gerar um dicionário JSON completo para preencher o template '{template_name}'.\n"
                        "**PROCESSO OBRIGATÓRIO:**\n"
                        "1. **INSPECIONE:** Primeiro, use a ferramenta `Inspetor de Placeholders de Template` com o nome do template '{template_name}' para obter a lista exata de TODAS as variáveis que ele espera.\n"
                        "2. **ANALISE E GERE:** Leia o histórico da conversa e a lista de placeholders que você obteve. Sua tarefa é criar um objeto JSON onde as **chaves são um espelho exato** dos placeholders encontrados. Para cada placeholder, gere o conteúdo apropriado com base na conversa.\n"
                        "   - Se um placeholder for um loop (ex: `secoes` ou `itens`), crie uma lista de objetos.\n"
                        "   - Infira valores para campos como títulos e datas a partir do contexto.\n"
                        "   - Se o usuário não forneceu dados para um placeholder, inclua a chave no JSON com um valor vazio (ex: `\"placeholder_desconhecido\": \"\"` ou `\"lista_vazia\": []`). NUNCA omita uma chave que a ferramenta de inspeção encontrou.\n\n"
                        f"--- HISTÓRICO DA CONVERSA ---\n{historico_texto}"
                    ),
                    # --- INÍCIO DA MUDANÇA CRÍTICA ---
                    expected_output=(
                        "Sua resposta final DEVE SER APENAS o bloco de código JSON, e NADA MAIS. "
                        "NÃO inclua nenhuma palavra, explicação, ou marcadores de linguagem como 'json' ou ```json. "
                        "A saída deve ser um JSON bruto, válido e diretamente parsável. "
                        "Exemplo de formato esperado: {\"chave1\": \"valor1\", \"chave2\": [{\"subchave\": \"subvalor\"}]}"
                    ),
                    # --- FIM DA MUDANÇA CRÍTICA ---
                    agent=analista
                )

                tarefa_preenchimento = Task(
                    description=(
                        f"Use a ferramenta 'TemplateFillerTool' para criar um documento. "
                        f"O nome do template a ser usado é EXATAMENTE '{template_name}'.\n"
                        f"Use o JSON da tarefa anterior como o contexto para preenchimento.\n"
                        f"O ID do usuário (owner_id) é '{user_id}'.\n"
                        f"Dê um nome de arquivo de saída apropriado ao documento final."
                    ),
                    expected_output="O resultado estruturado da ferramenta TemplateFillerTool.",
                    agent=especialista_doc,
                    context=[tarefa_analise]
                )
                tasks = [tarefa_analise, tarefa_preenchimento]
                crew_agents = [analista, especialista_doc]

        if intencao == 'CRIAR_DOCUMENTO_SIMPLES':
            # --- MUDANÇA: Detecção e injeção da extensão ---
            extensao = _extrair_extensao_desejada(historico_texto)
            logger.info(f"Extensão de arquivo simples detectada: {extensao}")

            tarefa_escrita = Task(
                description=(
                    "Escreva o conteúdo textual completo para o documento solicitado pelo usuário. "
                    f"Se for para uma planilha ({extensao == 'xlsx'}), estruture o texto de forma tabular, "
                    "usando quebras de linha para as linhas e algum separador (como vírgula ou ponto-e-vírgula) para as colunas."
                    f"\n--- HISTÓRICO ---\n{historico_texto}"
                ),
                expected_output="Um texto completo e bem formatado para o corpo do documento.",
                agent=revisor
            )
            tarefa_criacao = Task(
                description=(
                    "Use a ferramenta 'SimpleDocumentGeneratorTool' para transformar o texto da tarefa anterior em um arquivo. "
                    f"O ID do usuário (owner_id) é '{user_id}'.\n"
                    f"O nome do arquivo de saída DEVE terminar com a extensão '.{extensao}'. Dê um nome de arquivo apropriado."
                ),
                expected_output="O resultado estruturado da ferramenta SimpleDocumentGeneratorTool.",
                agent=especialista_doc,
                context=[tarefa_escrita]
            )
            tasks = [tarefa_escrita, tarefa_criacao]
            crew_agents = [revisor, especialista_doc]
            
        if not tasks: # Se nenhum pipeline foi montado (ex: CONVERSA_GERAL ou fallback)
            tarefa_conversa = Task(
                description=f"Formule uma resposta amigável e útil para a última pergunta ou comentário do usuário.\n--- HISTÓRICO ---\n{historico_texto}",
                expected_output="O texto da resposta final para o usuário.",
                agent=revisor
            )
            tasks = [tarefa_conversa]
            crew_agents = [revisor]

        # Passo 3: Executa a Crew com o pipeline sequencial e explícito
        crew = Crew(
            agents=crew_agents,
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
        )
        resultado_crew = crew.kickoff()
        
        # Passo 4: Processa o resultado final e salva no banco de dados
        logger.info("Resultado bruto da Crew: %s", str(resultado_crew))
        resposta_final = ""
        generated_doc_id = None
        try:
            # Tenta interpretar a saída como JSON (se a última tarefa usou uma ferramenta)
            dados_resultado = json.loads(str(resultado_crew))
            if isinstance(dados_resultado, dict):
                resposta_final = dados_resultado.get("message") or dados_resultado.get("content", str(dados_resultado))
                if dados_resultado.get("status") == "success" and dados_resultado.get("document_id"):
                    generated_doc_id = ObjectId(dados_resultado["document_id"])
        except (json.JSONDecodeError, TypeError):
            # Se não for JSON, trata como texto puro (se a última tarefa foi conversacional)
            resposta_final = str(resultado_crew)

        assistant_message = {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": resposta_final,
            "generated_document_id": generated_doc_id,
            "user_id": ObjectId(user_id),
            "timestamp": datetime.utcnow()
        }
        db.messages.insert_one(assistant_message)
        db.conversations.update_one({"_id": conversation_id}, {"$set": {"last_updated_at": datetime.utcnow()}})
        
        logger.info("Orquestração com roteamento para a mensagem %s concluída.", message_id)
        return "Sucesso"

    except Exception as e:
        logger.exception("ERRO CRÍTICO ao orquestrar a CrewAI para a mensagem %s: %s", message_id, e)
        if mensagem_atual:
            db.messages.insert_one({
                "conversation_id": mensagem_atual.get("conversation_id"),
                "role": "assistant",
                "content": "Ocorreu um erro interno grave ao processar sua solicitação. A equipe técnica foi notificada.",
                "user_id": mensagem_atual.get("user_id"),
                "timestamp": datetime.utcnow(),
                "is_error": True
            })
        return "Falha"