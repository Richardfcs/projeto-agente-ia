# /src/tasks/ia_processor.py

import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from bson import ObjectId
from crewai import Crew, Process, Task, Agent
from src.db.mongo import get_db

from src.services.intent_router import HybridIntentRouter, Intent
from src.services.memory_manager import PersistentConversationState
from flask import current_app
from src.utils.observability import log_with_context, track_performance, correlation_ctx

logger = log_with_context(component="IAProcessor")

# Carregador de Agentes
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

# --- INÍCIO DA MUDANÇA FINAL (ROTEADOR INTELIGENTE) ---

def _rotear_intencao(historico_cursor_list: List[Dict[str, Any]]) -> tuple[str, Dict]:
    """
    Nova versão: usa roteador híbrido antes de chamar LLM
    """
    ultima_mensagem_obj = next(
        (msg for msg in reversed(historico_cursor_list) 
         if msg.get('role') == 'user'), 
        None
    )
    
    if not ultima_mensagem_obj:
        return "CONVERSA_GERAL", {}
    
    ultima_mensagem_texto = ultima_mensagem_obj.get('content', '')
    
    # Usa o roteador híbrido
    router = HybridIntentRouter()
    intent, confidence, metadata = router.route(ultima_mensagem_texto, historico_cursor_list)
    
    # SÓ chama LLM se confiança muito baixa (apenas ~20% dos casos)
    if confidence < 0.4:
        logger.info("Confiança baixa, usando LLM para classificação")
        intent_llm = _classificar_intencao_por_ia(historico_cursor_list)
        return intent_llm, {}
    
    logger.info(f"Roteamento concluído sem LLM: {intent.value} (confiança: {confidence})")
    return intent.value, metadata

def _classificar_intencao_por_ia(historico_texto: str) -> str:
    """
    Usa um agente de IA para classificar a intenção.
    Serve como o fallback para o roteador híbrido.
    """
    agents = _ensure_agents()
    gerente = agents["gerente"]
    task = Task(
        description=(
            "Classifique a intenção da última mensagem do usuário. Categorias: "
            "'PREENCHER_TEMPLATE', 'CRIAR_DOCUMENTO_SIMPLES', 'LER_DOCUMENTO', 'CONVERSA_GERAL'.\n"
            "REGRAS: 1. 'CRIAR_DOCUMENTO_SIMPLES' é para pedidos explícitos de 'documento', etc. 2. 'CONVERSA_GERAL' é para pedidos genéricos como 'faça um poema'.\n\n"
            f"--- HISTÓRICO ---\n{historico_texto}"
        ),
        expected_output="Apenas a string da categoria.",
        agent=gerente,
    )
    crew = Crew(agents=[gerente], tasks=[task], verbose=0)
    return str(crew.kickoff()).strip()

# --- FIM DA MUDANÇA ---

# --- ÚLTIMA MENSAGEM PARA EXTRAÇÃO DE TEMPLATE ---
def _extrair_template_da_ultima_mensagem(historico_cursor: List[Dict[str, Any]], metadata: Dict = None) -> Optional[str]:
    """
    Usa metadata do roteador quando disponível (preservando case). Se não, tenta regex no texto original.
    """
    # Se o roteador já extraiu o nome, usa ele (preserva case)
    if metadata and metadata.get('matched_groups'):
        template_name = metadata['matched_groups'][0]
        if template_name:
            logger.info(f"Template extraído do metadata: {template_name}")
            return template_name

    # Fallback para regex tradicional usando o texto original (não lowercased)
    ultima_msg_usuario = next(
        (msg for msg in reversed(historico_cursor)
         if msg.get('role') == 'user'),
        None
    )

    if ultima_msg_usuario:
        content = ultima_msg_usuario.get('content', '')
        match = re.search(r"['\"]?([\w\d_\-]+\.docx?)['\"]?", content, re.IGNORECASE)
        if match:
            # match.group(1) preserva case do texto original
            template_name = match.group(1)
            logger.info(f"Template extraído por regex: {template_name}")
            return template_name

    return None

def _extrair_extensao_desejada(texto: str) -> str:
    """Detecta a extensão de arquivo solicitada no prompt e retorna 'docx', 'xlsx', ou 'pdf'."""
    texto_lower = texto.lower()
    if any(k in texto_lower for k in ['xlsx', 'planilha', 'excel']): return 'xlsx'
    if 'pdf' in texto_lower: return 'pdf'
    # DOCX é o padrão se nada for especificado
    return 'docx'

# --- Orquestrador Principal ---
@track_performance
def processar_solicitacao_ia(message_id: str) -> str:
    """
    Orquestra a equipe de IA classificando a intenção do usuário primeiro e
    depois construindo um pipeline de tarefas sequencial e explícito.
    """
    correlation_ctx.set_correlation_id(f"msg_{message_id}")
    logger.info("starting_orchestration", message_id=message_id)
    db = get_db()
    
    mensagem_atual = None
    try:
        # Garante que temos as instâncias dos agentes
        agents = _ensure_agents()
        analista, especialista_doc, revisor = agents["analista_de_conteudo"], agents["especialista_documentos"], agents["revisor_final"]

        # Obtém todo o contexto da conversa do banco de dados
        mensagem_atual = db.messages.find_one({"_id": ObjectId(message_id)})
        conversation_id, user_id = mensagem_atual["conversation_id"], str(mensagem_atual["user_id"])
        
        # --- USAR UMA LISTA ESTRUTURADA DO HISTÓRICO ---
        historico_cursor_list = list(db.messages.find({"conversation_id": conversation_id}).sort("timestamp", 1))
        
        # Instancia o gerenciador de estado da conversa
        estado_conversa = PersistentConversationState(db, str(conversation_id))
        
        # Roda o roteador para obter a intenção
        intencao, routing_metadata = _rotear_intencao(historico_cursor_list)
        logger.info("intention_detected", intention=intencao)
        
        # Cria o histórico de texto e o enriquece com a memória de curto prazo
        historico_texto = estado_conversa.injectar_contexto_no_prompt("\n".join([f"{msg.get('role')}: {msg.get('content')}" for msg in historico_cursor_list]))

        # Passo 2: Monta dinamicamente a lista de tarefas e agentes para a Crew
        tasks: List[Task] = []
        crew_agents: List[Agent] = []

        # --- TAREFA DE REVISÃO FINAL (REUTILIZÁVEL) ---
        tarefa_revisao_final = Task(
            description="Analise o resultado da tarefa anterior. Se for um JSON de sucesso, retorne o próprio JSON. Se for um JSON de erro (ex: 'Template não encontrado'), explique o problema de forma amigável e use a ferramenta `TemplateListerTool` para sugerir alternativas. Se for texto puro, apenas repasse o texto.",
            expected_output="A resposta final formatada. Se o input foi um JSON de sucesso, retorne-o. Senão, retorne uma mensagem de texto amigável.",
            agent=revisor
        )

        if intencao == 'PREENCHER_TEMPLATE':
            # --- CHAMAR A FUNÇÃO DE EXTRAÇÃO QUE FOCA NA ÚLTIMA MENSAGEM ---
            template_name = _extrair_template_da_ultima_mensagem(historico_cursor_list, routing_metadata)
            
            if not template_name:
                intencao = 'CONVERSA_GERAL'
                historico_texto += "\n\nsystem: A intenção era preencher um template, mas o nome do arquivo .docx não foi encontrado. A intenção foi alterada para CONVERSA_GERAL para pedir esclarecimento."
            else:
                logger.info("template_selected", template_name=template_name)
                
                tarefa_extracao_dados = Task(description=f"Gere o conteúdo JSON para o template '{template_name}' com base neste histórico:\n{historico_texto}", expected_output="Apenas o bloco JSON do conteúdo.", agent=analista)
                tarefa_preenchimento = Task(description=f"Chame a ferramenta `TemplateFillerTool` com `template_name`='{template_name}', `owner_id`='{user_id}', e o `context` da tarefa anterior.", expected_output="O resultado JSON da ferramenta `TemplateFillerTool`.", agent=especialista_doc, context=[tarefa_extracao_dados])
                
                # A tarefa de revisão é adicionada ao final do pipeline
                tarefa_revisao_final.context = [tarefa_preenchimento]
                tasks = [tarefa_extracao_dados, tarefa_preenchimento, tarefa_revisao_final]
                crew_agents = [analista, especialista_doc, revisor]

        elif intencao == 'LER_DOCUMENTO':
            # Extrai o nome do arquivo da mensagem
            doc_name_match = re.search(r"\'?([\w\d_-]+\.docx)\'?", historico_texto, re.IGNORECASE)
            if not doc_name_match:
                intencao = 'CONVERSA_GERAL' # Fallback se não encontrar o nome do arquivo
            else:
                doc_name = doc_name_match.group(1)
                logger.info(f"Documento a ser lido: {doc_name}")

                # Encontra o ID do documento no banco de dados
                # NOTA: Esta busca assume que o nome do arquivo é único para o usuário.
                # Uma busca mais robusta usaria o contexto para encontrar o ID exato.
                document_meta = db.documents.find_one({"filename": doc_name, "owner_id": ObjectId(user_id)})
                if not document_meta:
                    # Se não encontrar, o revisor vai lidar com o erro de forma amigável
                    historico_texto += f"\n\nsystem: O usuário pediu para ler o documento '{doc_name}', mas ele não foi encontrado no banco de dados."
                    intencao = 'CONVERSA_GERAL'
                else:
                    document_id_to_read = str(document_meta['_id'])
                    
                    # Tarefa para o especialista ler o arquivo
                    tarefa_leitura = Task(
                        description=f"Use a ferramenta `FileReaderTool` para ler o conteúdo do documento com o ID '{document_id_to_read}'.",
                        expected_output="O resultado da ferramenta FileReaderTool, contendo o conteúdo do arquivo.",
                        agent=especialista_doc
                    )
                    
                    # Tarefa para o revisor resumir e apresentar o conteúdo
                    tarefa_apresentacao = Task(
                        description="Analise o conteúdo extraído da tarefa anterior e apresente-o de forma clara e resumida para o usuário.",
                        expected_output="Um resumo em texto simples do conteúdo do documento.",
                        agent=revisor,
                        context=[tarefa_leitura]
                    )
                    
                    tasks = [tarefa_leitura, tarefa_apresentacao]
                    crew_agents = [especialista_doc, revisor]

        if intencao == 'CRIAR_DOCUMENTO_SIMPLES':
            # Esta seção lida com a criação de documentos a partir do zero
            extensao = _extrair_extensao_desejada(historico_texto)
            prompt_escrita = f"Gere dados para uma planilha Excel (separados por ';')." if extensao == 'xlsx' else "Escreva o conteúdo completo para o documento."
            
            tarefa_escrita = Task(description=f"{prompt_escrita}\n\n--- HISTÓRICO ---\n{historico_texto}", expected_output="O texto completo.", agent=revisor)
            tarefa_criacao = Task(description=f"Use `SimpleDocumentGeneratorTool` para criar um arquivo. O owner_id é '{user_id}', e o nome deve terminar com '.{extensao}'.", expected_output="O resultado JSON da ferramenta.", agent=especialista_doc, context=[tarefa_escrita])

            # A tarefa de revisão é adicionada também a este pipeline
            tarefa_revisao_final.context = [tarefa_criacao]
            tasks = [tarefa_escrita, tarefa_criacao, tarefa_revisao_final]
            crew_agents = [revisor, especialista_doc, revisor]
            
        if not tasks: # Se nenhum pipeline foi montado (ex: CONVERSA_GERAL ou fallback)
            # Esta seção lida com todas as outras interações
            tarefa_conversa = Task(
                description=(
                    "Sua tarefa é ser um assistente de IA útil e responder à ÚLTIMA MENSAGEM DO USUÁRIO de forma coerente.\n\n"
                    "**CENÁRIO 1 (PEDIDO CRIATIVO):** Se a última mensagem do usuário for um pedido criativo (piada, poema, resumo), ATENDA O PEDIDO diretamente.\n\n"
                    "**CENÁRIO 2 (CONFIRMAÇÃO DE TEMPLATE):** Se o histórico mostra que a sua última resposta foi uma pergunta de confirmação para usar um template (ex: 'Você quer que eu use o template...?') e a última mensagem do usuário é uma resposta afirmativa (ex: 'sim', 'exatamente', 'isso mesmo'), sua ÚNICA tarefa é instruir o usuário sobre o próximo passo. Responda de forma clara: 'Ótimo! Para que eu possa criar o documento, por favor, envie uma nova mensagem com o comando completo, por exemplo: `Use o template 'TEMPLATE_TPF.docx' para criar um documento sobre energias renováveis.`'.\n\n"
                    "**CENÁRIO 3 (OUTRAS PERGUNTAS):** Para todas as outras perguntas, responda da melhor forma possível. Se a pergunta for sobre quais templates existem, use a ferramenta `TemplateListerTool` para responder.\n\n"
                    f"--- HISTÓRICO COMPLETO DA CONVERSA ---\n{historico_texto}"
                ),
                expected_output="A resposta final em texto para o usuário.",
                agent=revisor
            )
            tasks = [tarefa_conversa]
            crew_agents = [revisor]

        # Passo 3: Executa a Crew com o pipeline sequencial e explícito
        crew = Crew(
            agents=list(set(crew_agents)), # Usa set para evitar agentes duplicados
            tasks=tasks,
            process=Process.sequential,
            verbose=True,
        )
        resultado_crew = crew.kickoff()

        estado_conversa.update_from_tool_output(str(resultado_crew))
        
        # Passo 4: Processa o resultado final e salva no banco de dados
        logger.info("crew_raw_result", result_length=len(str(resultado_crew)))
    
        resposta_final, generated_doc_id = str(resultado_crew), None

        
        # A resposta final idealmente é o que a última tarefa (revisão) retornou.
        # Tentamos extrair o ID do documento da resposta para o frontend.
        try:
            dados_resultado = json.loads(resposta_final)
            if isinstance(dados_resultado, dict):
                 # Se a tarefa de revisão retornou o JSON de sucesso, o frontend o recebe.
                 if dados_resultado.get("status") == "success" and dados_resultado.get("document_id"):
                    generated_doc_id = ObjectId(dados_resultado["document_id"])
        except (json.JSONDecodeError, TypeError):
            # A resposta final é texto amigável do revisor.
            resposta_final = str(resultado_crew)
            match_id = re.search(r"document_id': '([a-fA-F0-9]{24})'", resposta_final)
            if match_id:
                generated_doc_id = ObjectId(match_id.group(1))

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
        
        logger.info("orchestration_completed", message_id=message_id)
        return "Sucesso"

    except Exception as e:
        logger.exception("critical_orchestration_error", message_id=message_id, error=str(e), error_type=type(e).__name__)
        if mensagem_atual:
            db.messages.insert_one({
                "conversation_id": mensagem_atual.get("conversation_id"),
                "role": "assistant",
                "content": "Ocorreu um erro interno grave.",
                "user_id": mensagem_atual.get("user_id"),
                "timestamp": datetime.utcnow(),
                "is_error": True
            })
        return "Falha"