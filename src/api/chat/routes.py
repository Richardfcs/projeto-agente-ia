# Arquivo: /src/api/chat/routes.py

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime
from src.db.mongo import get_db
from src.tasks.ia_processor import processar_solicitacao_ia

chat_bp = Blueprint('chat_bp', __name__)

@chat_bp.route('/conversations', methods=['POST'])
@jwt_required()
def send_message():
    """Inicia uma nova conversa ou envia uma mensagem e aguarda o processamento."""
    current_user_id = get_jwt_identity()
    data = request.get_json()
    prompt = data.get('prompt')
    conversation_id_str = data.get('conversation_id')

    if not prompt:
        return jsonify({"erro": "O campo 'prompt' é obrigatório"}), 400

    db = get_db()
    
    if not conversation_id_str:
        new_conv = {
            "user_id": ObjectId(current_user_id),
            "title": prompt[:70] + ("..." if len(prompt) > 70 else ""),
            "created_at": datetime.utcnow(),
            "last_updated_at": datetime.utcnow()
        }
        result = db.conversations.insert_one(new_conv)
        conversation_id = result.inserted_id
    else:
        try:
            conversation_id = ObjectId(conversation_id_str)
        except InvalidId:
            return jsonify({"erro": "ID de conversa inválido"}), 400

    user_message = {
        "conversation_id": conversation_id,
        "role": "user",
        "content": prompt,
        "user_id": ObjectId(current_user_id),
        "timestamp": datetime.utcnow()
    }
    msg_result = db.messages.insert_one(user_message)

    # Chama a função de processamento diretamente e aguarda o resultado.
    resultado = processar_solicitacao_ia(str(msg_result.inserted_id))

    if resultado == "Sucesso":
        return jsonify({
            "mensagem": "Sua solicitação foi processada com sucesso.",
            "conversation_id": str(conversation_id),
            "message_id": str(msg_result.inserted_id)
        }), 201
    else:
        return jsonify({"erro": "Ocorreu um problema no servidor ao processar sua solicitação."}), 500

@chat_bp.route('/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    """Lista todas as conversas do usuário logado."""
    current_user_id = get_jwt_identity()
    db = get_db()
    
    convs_cursor = db.conversations.find(
        {"user_id": ObjectId(current_user_id)}
    ).sort("last_updated_at", -1)
    
    conversations = []
    for conv in convs_cursor:
        conv['_id'] = str(conv['_id'])
        conv['user_id'] = str(conv['user_id'])
        conversations.append(conv)
        
    return jsonify(conversations)

@chat_bp.route('/conversations/<string:conversation_id_str>', methods=['GET'])
@jwt_required()
def get_conversation_history(conversation_id_str):
    """Lista todas as mensagens de uma conversa específica."""
    current_user_id = get_jwt_identity()
    db = get_db()
    
    try:
        conversation_id = ObjectId(conversation_id_str)
    except InvalidId:
        return jsonify({"erro": "ID de conversa inválido"}), 400
    
    conv = db.conversations.find_one({"_id": conversation_id, "user_id": ObjectId(current_user_id)})
    if not conv:
        return jsonify({"erro": "Conversa não encontrada ou acesso negado"}), 404
    
    msgs_cursor = db.messages.find(
        {"conversation_id": conversation_id}
    ).sort("timestamp", 1)
    
    messages = []
    for msg in msgs_cursor:
        msg['_id'] = str(msg['_id'])
        msg['conversation_id'] = str(msg['conversation_id'])
        msg['user_id'] = str(msg['user_id'])
        if 'generated_document_id' in msg and msg['generated_document_id']:
             msg['generated_document_id'] = str(msg['generated_document_id'])
        messages.append(msg)
        
    return jsonify(messages)