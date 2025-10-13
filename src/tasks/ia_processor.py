# /src/tasks/ia_processor.py (versão final, completa e robusta)

import json
import logging
import re
from datetime import datetime
from typing import Dict, Any, List, Optional

from bson import ObjectId
from crewai import Crew, Process, Task
from src.db.mongo import get_db

logger = logging.getLogger(__name__)

# --- Funções de Suporte (sem alterações) ---
try: from src.tasks.agents import create_agents
except ImportError: create_agents = None
_module_agents: Dict[str, Any] = {}
def _ensure_agents() -> Dict[str, Any]:
    global _module_agents
    try: from flask import current_app; app_agents = getattr(current_app, "agents", None);_module_agents = app_agents or _module_agents; return _module_agents
    except (RuntimeError, AttributeError): pass
    if _module_agents: return _module_agents
    if create_agents is None: raise RuntimeError("Fábrica de agentes não pôde ser importada.")
    try: _module_agents = create_agents(); return _module_agents
    except Exception as e: raise RuntimeError(f"Erro ao instanciar agentes: {e}") from e
def _rotear_intencao(historico_cursor_list: List[Dict[str, Any]]) -> str:
    ultima_mensagem_obj = next((msg for msg in reversed(historico_cursor_list) if msg.get('role') == 'user'), None)
    if ultima_mensagem_obj:
        ultima_mensagem_texto = ultima_mensagem_obj.get('content', '').lower()
        if '.docx' in ultima_mensagem_texto: return 'PREENCHER_TEMPLATE'
    historico_texto = "\n".join([f"{msg.get('role')}: {msg.get('content')}" for msg in historico_cursor_list])
    agents = _ensure_agents(); gerente = agents["gerente"]
    task = Task(description=f"Classifique a intenção da última mensagem: {historico_texto}", expected_output="Apenas a categoria.", agent=gerente)
    crew = Crew(agents=[gerente], tasks=[task], verbose=0); return str(crew.kickoff()).strip()
def _extrair_template_da_ultima_mensagem(historico_cursor: List[Dict[str, Any]]) -> Optional[str]:
    ultima_msg_usuario = next((msg for msg in reversed(historico_cursor) if msg.get('role') == 'user'), None)
    if ultima_msg_usuario:
        content = ultima_msg_usuario.get('content', '')
        match = re.search(r"\'?([\w\d_-]+\.docx)\'?", content, re.IGNORECASE)
        if match: return match.group(1)
    return None
def _extrair_extensao_desejada(texto: str) -> str:
    texto_lower = texto.lower()
    if any(k in texto_lower for k in ['xlsx', 'planilha', 'excel']): return 'xlsx'
    if 'pdf' in texto_lower: return 'pdf'
    return 'docx'


# --- Orquestrador Principal ---
def processar_solicitacao_ia(message_id: str) -> str:
    """ Orquestra a equipe de IA classificando a intenção e construindo o pipeline de tarefas. """
    logger.info("Iniciando orquestração para a mensagem: %s", message_id)
    db = get_db()
    
    mensagem_atual = None
    try:
        agents = _ensure_agents()
        analista, especialista_doc, revisor = agents["analista_de_conteudo"], agents["especialista_documentos"], agents["revisor_final"]

        mensagem_atual = db.messages.find_one({"_id": ObjectId(message_id)})
        conversation_id, user_id = mensagem_atual["conversation_id"], str(mensagem_atual["user_id"])
        
        historico_cursor_list = list(db.messages.find({"conversation_id": conversation_id}).sort("timestamp", 1))
        
        intencao = _rotear_intencao(historico_cursor_list)
        logger.info(f"Intenção detectada: {intencao}")
        
        historico_texto = "\n".join([f"{msg.get('role')}: {msg.get('content')}" for msg in historico_cursor_list])

        tasks, crew_agents = [], []

        if intencao == 'PREENCHER_TEMPLATE':
            template_name = _extrair_template_da_ultima_mensagem(historico_cursor_list)
            
            if not template_name:
                intencao = 'CONVERSA_GERAL'
            else:
                logger.info(f"Template a ser usado: {template_name}")
                
                tarefa_extracao_dados = Task(description=f"Gere o conteúdo JSON para o template '{template_name}' com base no histórico: {historico_texto}", expected_output="Apenas o bloco JSON do conteúdo.", agent=analista)
                tarefa_preenchimento = Task(description=f"Chame `TemplateFillerTool` com `template_name`='{template_name}', `owner_id`='{user_id}', e o `context` da tarefa anterior.", expected_output="O resultado JSON da ferramenta.", agent=especialista_doc, context=[tarefa_extracao_dados])
                
                tasks = [tarefa_extracao_dados, tarefa_preenchimento]
                crew_agents = [analista, especialista_doc]

        elif intencao == 'CRIAR_DOCUMENTO_SIMPLES':
            extensao = _extrair_extensao_desejada(historico_texto)
            prompt_escrita = f"Gere dados para uma planilha (separados por ';')." if extensao == 'xlsx' else "Escreva o conteúdo para o documento."
            
            tarefa_escrita = Task(description=f"{prompt_escrita}\n\nHistórico: {historico_texto}", expected_output="O texto completo.", agent=revisor)
            tarefa_criacao = Task(description=f"Use `SimpleDocumentGeneratorTool`. O owner_id é '{user_id}', e o nome deve terminar com '.{extensao}'.", expected_output="O resultado JSON da ferramenta.", agent=especialista_doc, context=[tarefa_escrita])
            
            tasks = [tarefa_escrita, tarefa_criacao]
            crew_agents = [revisor, especialista_doc]
            
        # --- INÍCIO DA CORREÇÃO ---
        # Se nenhuma das lógicas acima preencheu as tarefas (ou seja, a intenção é CONVERSA_GERAL),
        # então criamos a tarefa de conversação aqui.
        if not tasks:
            tarefa_conversa = Task(
                description=(
                    "Responda à ÚLTIMA MENSAGEM DO USUÁRIO. "
                    "Se o pedido for criativo (piada, poema), atenda. "
                    "Se for sobre templates, use `TemplateListerTool`. "
                    "Se o usuário confirmar o uso de um template sem dizer o nome, instrua-o a fazer um novo pedido claro, ex: `Use o template 'TEMPLATE_TPF.docx' para...`.\n\n"
                    f"Histórico: {historico_texto}"
                ),
                expected_output="A resposta em texto para o usuário.",
                agent=revisor
            )
            tasks = [tarefa_conversa]
            crew_agents = [revisor] # <-- Esta linha estava faltando
        # --- FIM DA CORREÇÃO ---

        crew = Crew(agents=list(set(crew_agents)), tasks=tasks, process=Process.sequential, verbose=True)
        resultado_crew = crew.kickoff()
        
        logger.info("Resultado bruto da Crew: %s", str(resultado_crew))
        
        resposta_final, generated_doc_id = str(resultado_crew), None
        
        try:
            dados_resultado = json.loads(resposta_final)
            if isinstance(dados_resultado, dict):
                resposta_final = str(dados_resultado)
                if dados_resultado.get("status") == "success" and dados_resultado.get("document_id"):
                    generated_doc_id = ObjectId(dados_resultado["document_id"])
        except (json.JSONDecodeError, TypeError):
            resposta_final = str(resultado_crew)
        
        assistant_message = {"conversation_id": conversation_id, "role": "assistant", "content": resposta_final, "generated_doc_id": generated_doc_id, "user_id": ObjectId(user_id), "timestamp": datetime.utcnow()}
        db.messages.insert_one(assistant_message)
        db.conversations.update_one({"_id": conversation_id}, {"$set": {"last_updated_at": datetime.utcnow()}})
        
        logger.info("Orquestração concluída para a mensagem %s.", message_id)
        return "Sucesso"

    except Exception as e:
        logger.exception("ERRO CRÍTICO ao orquestrar a CrewAI para a mensagem %s: %s", message_id, e)
        if mensagem_atual:
            db.messages.insert_one({"conversation_id": mensagem_atual.get("conversation_id"), "role": "assistant", "content": "Ocorreu um erro interno grave.", "user_id": mensagem_atual.get("user_id"), "timestamp": datetime.utcnow(), "is_error": True})
        return "Falha"