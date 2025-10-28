# src/tasks/ia_processor.py

"""
Orquestrador de IA Refatorado com LangGraph.

Este módulo atua como uma fina camada de orquestração que conecta a API Flask
ao nosso robusto grafo de execução de IA.

Responsabilidades:
1.  Receber o ID de uma mensagem do usuário.
2.  Carregar o contexto necessário do banco de dados (histórico da conversa).
3.  Montar o objeto de `estado inicial` para o grafo.
4.  Invocar o grafo `app_graph` com esse estado.
5.  Receber o `estado final` do grafo.
6.  Salvar a resposta do assistente e quaisquer artefatos (como IDs de documentos) no banco de dados.
7.  Lidar com exceções de forma centralizada.

Esta abordagem substitui a lógica manual, complexa e condicional da implementação
anterior por uma única chamada a um sistema determinístico e com estado.
"""

from bson import ObjectId
from datetime import datetime
from src.db.mongo import get_db
from src.utils.observability import log_with_context, track_performance, correlation_ctx

# Importa o grafo compilado, que é o coração da nossa nova lógica de IA
from src.tasks.graph.builder import app_graph

logger = log_with_context(component="IAProcessor-LangGraph")


@track_performance
def processar_solicitacao_ia(message_id: str) -> str:
    """
    Orquestra o processamento de uma solicitação de IA usando o grafo LangGraph.

    Args:
        message_id: O ID da mensagem do usuário que acionou o processamento.

    Returns:
        "Sucesso" se o processo foi concluído, "Falha" caso contrário.
    """
    correlation_ctx.set_correlation_id(f"msg_{message_id}")
    logger.info("Iniciando orquestração com LangGraph", message_id=message_id)
    db = get_db()
    
    # É crucial ter um bloco try/except para capturar qualquer falha no pipeline
    try:
        # --- ETAPA 1: Preparar os Dados de Entrada ---
        # Busca a mensagem atual e todo o histórico da conversa no banco de dados.
        current_message = db.messages.find_one({"_id": ObjectId(message_id)})
        if not current_message:
            logger.error("Mensagem não encontrada no DB", message_id=message_id)
            return "Falha"

        conversation_id = current_message["conversation_id"]
        user_id = current_message["user_id"]
        
        # O histórico é essencial para o contexto do LLM
        history_cursor = db.messages.find({"conversation_id": conversation_id}).sort("timestamp", 1)
        # Converte o cursor para uma lista e ObjectIds para strings para serialização
        conversation_history = [
            {**msg, "_id": str(msg["_id"])} for msg in history_cursor
        ]

        # --- ETAPA 2: Montar o Estado Inicial para o Grafo ---
        # Este dicionário deve corresponder exatamente à estrutura definida em `GraphState`.
        initial_state = {
            "user_id": str(user_id),
            "conversation_id": str(conversation_id),
            "prompt": current_message["content"],
            "input_document_id": str(current_message.get("input_document_id")) if current_message.get("input_document_id") else None,
            "conversation_history": conversation_history,
        }
        logger.info("Estado inicial montado. Invocando o grafo.", conversation_id=str(conversation_id))

        # --- ETAPA 3: Invocar o Grafo ---
        # Esta é a chamada principal. Toda a lógica complexa de IA é executada aqui.
        # ATENÇÃO: Esta é uma chamada síncrona/bloqueante.
        final_state = app_graph.invoke(initial_state)

        # --- ETAPA 4: Salvar o Resultado Final no Banco de Dados ---
        # Extrai a resposta e o ID do documento gerado (se houver) do estado final.
        final_response_content = final_state.get("final_response", "Desculpe, ocorreu um erro e não consegui gerar uma resposta.")
        generated_doc_id = final_state.get("generated_document_id")

        assistant_message = {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": final_response_content,
            "generated_document_id": ObjectId(generated_doc_id) if generated_doc_id else None,
            "user_id": user_id,
            "timestamp": datetime.utcnow(),
        }
        db.messages.insert_one(assistant_message)
        
        # Atualiza a data da última modificação da conversa
        db.conversations.update_one(
            {"_id": conversation_id},
            {"$set": {"last_updated_at": datetime.utcnow()}}
        )
        
        logger.info("Orquestração com LangGraph concluída com sucesso.", message_id=message_id)
        return "Sucesso"

    except Exception as e:
        logger.exception(
            "Erro crítico na orquestração do LangGraph.",
            message_id=message_id,
            error=str(e),
            error_type=type(e).__name__
        )
        # Tenta salvar uma mensagem de erro no chat para que o usuário não fique sem resposta
        if 'current_message' in locals() and current_message:
            db.messages.insert_one({
                "conversation_id": current_message.get("conversation_id"),
                "role": "assistant",
                "content": "Ocorreu um erro interno no servidor ao processar sua solicitação. A equipe de desenvolvimento já foi notificada.",
                "user_id": current_message.get("user_id"),
                "timestamp": datetime.utcnow(),
                "is_error": True
            })
        return "Falha"