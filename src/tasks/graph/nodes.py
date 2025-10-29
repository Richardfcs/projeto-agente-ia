# src/tasks/graph/nodes.py

"""
Implementação dos Nós do Grafo de IA (Os Músculos da Nossa Lógica).

Este arquivo contém as funções que executam o trabalho real em cada etapa
do nosso fluxo de IA, conforme definido em `builder.py`.

Cada função aqui é um "nó" e segue um contrato simples:
1.  Recebe o `GraphState` atual como seu único argumento.
2.  Executa uma tarefa específica (rotear, chamar um LLM, usar uma ferramenta, etc.).
3.  Retorna um dicionário contendo apenas as chaves do `GraphState` que deseja atualizar.

Esta abordagem modular torna o sistema fácil de entender, manter e estender.
"""

import json
import re
from typing import Dict, Any

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import JsonOutputParser

from src.config import Config
from .state import GraphState
from src.services.intelligent_router import IntelligentRouter, CreateDocument, FillTemplate, ReadDocument, GeneralChat
from src.tasks.tools import (
    template_lister_tool,
    template_inspector_tool,
    template_filler_tool,
    file_reader_tool,
    simple_document_generator_tool,
)
from src.utils.observability import log_with_context

logger = log_with_context(component="GraphNodes")

# --- Inicialização do LLM (Gemini) ---
# Instanciamos o LLM uma única vez para ser reutilizado por todos os nós.
# `convert_system_message_to_human=True` é uma boa prática para compatibilidade com Gemini.
try:
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        google_api_key=Config.GOOGLE_API_KEY,
        convert_system_message_to_human=True,
        temperature=0.7,
    )
    logger.info("LLM Gemini inicializado com sucesso.")
except Exception as e:
    logger.error("Falha ao inicializar o LLM Gemini. Verifique a GOOGLE_API_KEY.", error=str(e))
    llm = None

# --- Helper Functions (Extraídas do antigo ia_processor) ---

def _get_template_name_from_state(state: GraphState) -> str | None:
    """Extrai o nome do arquivo do template do prompt do usuário."""
    match = re.search(r"['\"]?([\w\d_\-]+\.docx?)['\"]?", state["prompt"], re.IGNORECASE)
    return match.group(1) if match else None

# --- Implementação dos Nós ---

def router_node(state: GraphState) -> Dict[str, Any]:
    """
    Primeiro nó do grafo: usa o roteador inteligente baseado em Tool Calling
    para classificar a intenção do usuário de forma semântica.
    """
    logger.info("Executando router_node (Intelligent)", conversation_id=state["conversation_id"])
    if not llm:
        return {"final_response": "Erro crítico: O modelo de linguagem (LLM) não está disponível."}
        
    router = IntelligentRouter()
    has_attachment = bool(state.get("input_document_id"))
    
    # Roteia e obtém uma classe Pydantic como resultado
    routed_tool = router.route(state["prompt"], state["conversation_history"], has_attachment)
    
    tool_name, tool_args = router.route(state["prompt"], state["conversation_history"], has_attachment)
    
    logger.info(f"Intenção roteada para: {tool_name} com args: {tool_args}")

    # Armazena a chamada completa no estado para os próximos nós usarem
    return {"routed_tool_call": {"tool": tool_name, "args": tool_args}}

def fill_template_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Executa o fluxo completo para preencher um template:
    1. Inspeciona o template para obter os campos necessários.
    2. Usa o LLM para gerar um JSON com o conteúdo.
    3. Chama a ferramenta para preencher o documento.
    """
    logger.info("Executando fill_template_flow_node", conversation_id=state["conversation_id"])
    template_name = _get_template_name_from_state(state)

    if not template_name:
        logger.warning("Nenhum nome de template encontrado no prompt.", prompt=state["prompt"])
        return {
            "final_response": "Não consegui identificar qual template você gostaria de usar. Por favor, mencione o nome completo do arquivo (ex: 'proposta_comercial.docx')."
        }

    # 1. Inspecionar o template
    inspector_result = template_inspector_tool.invoke({"template_name": template_name})
    if inspector_result.get("status") == "error":
        return {"tool_output": inspector_result} # Deixa o nó final formatar o erro

    required_fields = inspector_result.get("data", {}).get("required_top_level", [])
    
    # 2. Gerar JSON com o LLM
    prompt_template = f"""
    Histórico da Conversa:
    {state['conversation_history']}

    Última Mensagem do Usuário:
    {state['prompt']}
    
    Sua tarefa é extrair as informações da conversa e gerar um JSON para preencher o template '{template_name}'.
    O JSON deve conter as seguintes chaves: {required_fields}.
    Responda APENAS com o bloco JSON.
    """
    
    parser = JsonOutputParser()
    chain = llm | parser
    
    try:
        generated_json = chain.invoke(prompt_template)
    except OutputParserException as e:
        logger.error("Falha ao parsear a saída do LLM como JSON.", error=str(e))
        return {
            "final_response": "Tive um problema ao estruturar os dados para o seu documento. Você poderia tentar reformular seu pedido?"
        }

    # 3. Chamar a ferramenta para preencher o documento
    filler_result = template_filler_tool.invoke({
        "template_name": template_name,
        "context": generated_json,
        "owner_id": state["user_id"]
    })
    
    return {"tool_output": filler_result}

def read_document_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Executa o fluxo de leitura de um documento anexado e resume seu conteúdo.
    """
    logger.info("Executando read_document_flow_node", conversation_id=state["conversation_id"])
    document_id = state.get("input_document_id")

    if not document_id:
        return {"final_response": "Por favor, anexe um documento para que eu possa lê-lo."}

    # 1. Ler o conteúdo do arquivo
    reader_result = file_reader_tool.invoke({"document_id": document_id})
    if reader_result.get("status") == "error":
        return {"tool_output": reader_result}

    file_content = reader_result.get("data", {}).get("content", "")
    
    # 2. Usar o LLM para resumir ou responder com base no conteúdo
    prompt = f"""
    O usuário enviou a seguinte mensagem: '{state['prompt']}'.
    O conteúdo do documento anexado é:
    ---
    {file_content}
    ---
    Com base no conteúdo do documento, responda à solicitação do usuário.
    """
    response = llm.invoke(prompt)
    return {"final_response": response.content}

def create_document_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Executa o fluxo para criar um documento simples (docx, xlsx, pdf) do zero.
    """
    logger.info("Executando create_document_flow_node", conversation_id=state["conversation_id"])
    
    # 1. Usar o LLM para gerar o conteúdo textual
    prompt = f"""
    Histórico da Conversa:
    {state['conversation_history']}

    Última Mensagem do Usuário:
    {state['prompt']}
    
    Sua tarefa é escrever o conteúdo textual completo para atender ao pedido do usuário.
    Se o pedido for para uma planilha, separe as colunas com ';' e as linhas com quebras de linha.
    Responda APENAS com o conteúdo a ser escrito no arquivo.
    """
    generated_content = llm.invoke(prompt).content

    # 2. Determinar a extensão e chamar a ferramenta
    prompt_lower = state["prompt"].lower()
    if any(k in prompt_lower for k in ['xlsx', 'planilha', 'excel']):
        ext = 'xlsx'
    elif 'pdf' in prompt_lower:
        ext = 'pdf'
    else:
        ext = 'docx' # Padrão
    
    filename = f"documento_gerado_{state['conversation_id'][-6:]}.{ext}"

    creator_result = simple_document_generator_tool.invoke({
        "output_filename": filename,
        "content": generated_content,
        "owner_id": state["user_id"]
    })

    return {"tool_output": creator_result}

def general_chat_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Nó de fallback para conversas gerais, Q&A, e outras intenções simples.
    """
    logger.info("Executando general_chat_flow_node", conversation_id=state["conversation_id"])
    
    # Caso especial: se a intenção era listar templates, usamos a ferramenta
    if state.get("intent") == "LISTAR_TEMPLATES":
        lister_result = template_lister_tool.invoke({})
        templates = lister_result.get("data", {}).get("templates", [])
        if templates:
            response_text = "Claro! Os templates disponíveis são:\n- " + "\n- ".join(templates)
        else:
            response_text = "No momento, não há templates disponíveis no sistema."
        return {"final_response": response_text}

    # Para outras conversas, apenas chamamos o LLM com o histórico
    prompt = f"""
    Você é um assistente de IA prestativo. Responda à última mensagem do usuário de forma concisa e útil,
    considerando o histórico da conversa.

    Histórico:
    {state['conversation_history']}

    Última Mensagem:
    {state['prompt']}
    """
    response = llm.invoke(prompt)
    return {"final_response": response.content}

def final_response_node(state: GraphState) -> Dict[str, Any]:
    """
    Nó final que formata a saída de uma ferramenta em uma resposta amigável para o usuário.
    Se a resposta final já foi definida, ele apenas a repassa.
    """
    logger.info("Executando final_response_node", conversation_id=state["conversation_id"])
    
    if state.get("final_response"):
        # A resposta já foi gerada por um nó anterior (ex: chat geral), não faz nada.
        return {}

    tool_output = state.get("tool_output")
    if not tool_output:
        return {"final_response": "Desculpe, não consegui processar seu pedido."}

    if tool_output.get("status") == "success":
        final_message = tool_output.get("message", "Sua solicitação foi processada com sucesso!")
        doc_id = tool_output.get("data", {}).get("document_id")
        return {
            "final_response": final_message,
            "generated_document_id": doc_id
        }
    else: # status == "error"
        error_message = tool_output.get("message", "Ocorreu um erro desconhecido.")
        # Lógica para tornar o erro mais útil
        if tool_output.get("error_code") == "TEMPLATE_NOT_FOUND":
            lister_result = template_lister_tool.invoke({})
            templates = lister_result.get("data", {}).get("templates", [])
            if templates:
                error_message += "\n\nQue tal tentar um destes?:\n- " + "\n- ".join(templates)
        
        return {"final_response": error_message}