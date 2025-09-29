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
    """
    Inicia uma nova conversa ou envia uma mensagem para uma existente.
    Agora com a capacidade de anexar um documento de entrada.
    """
    current_user_id = get_jwt_identity()
    data = request.get_json()

    # Pega os dados do corpo da requisição
    prompt = data.get('prompt')
    conversation_id_str = data.get('conversation_id') # Opcional
    input_document_id_str = data.get('input_document_id') # Opcional

    if not prompt:
        return jsonify({"erro": "O campo 'prompt' é obrigatório"}), 400

    db = get_db()
    
    # Se não houver um ID de conversa, cria uma nova.
    # Se houver, valida o ID e o utiliza.
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
            # Verificação de segurança: garante que a conversa pertence ao usuário logado
            conv_check = db.conversations.find_one({
                "_id": conversation_id, 
                "user_id": ObjectId(current_user_id)
            })
            if not conv_check:
                return jsonify({"erro": "Conversa não encontrada ou acesso negado"}), 404
        except InvalidId:
            return jsonify({"erro": "ID de conversa inválido"}), 400

    # Constrói o documento da mensagem do usuário
    user_message = {
        "conversation_id": conversation_id,
        "role": "user",
        "content": prompt,
        "user_id": ObjectId(current_user_id),
        "timestamp": datetime.utcnow()
    }
    
    # Se um documento foi anexado, adiciona sua referência à mensagem
    if input_document_id_str:
        try:
            doc_id = ObjectId(input_document_id_str)
            # Verificação de segurança: garante que o documento pertence ao usuário logado
            doc_check = db.documents.find_one({
                "_id": doc_id,
                "owner_id": ObjectId(current_user_id)
            })
            if not doc_check:
                return jsonify({"erro": "Documento anexado não encontrado ou acesso negado"}), 404
            
            # Adiciona a referência ao documento de metadados na mensagem
            user_message["input_document_id"] = doc_id
        except InvalidId:
            return jsonify({"erro": "ID de documento anexado inválido"}), 400

    # Insere a mensagem final no banco de dados
    msg_result = db.messages.insert_one(user_message)

    # Chama a função de processamento de IA diretamente (fluxo síncrono)
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

@chat_bp.route('/conversations/<string:conversation_id>', methods=['DELETE'])
@jwt_required()
def delete_conversation(conversation_id):
    """Exclui uma conversa inteira e todas as suas mensagens e arquivos associados."""
    current_user_id = get_jwt_identity()
    db = get_db()
    fs = get_gridfs()
    conv_oid = ObjectId(conversation_id)

    # 1. Verificar se a conversa pertence ao usuário antes de fazer qualquer coisa
    conversation_to_delete = db.conversations.find_one({
        "_id": conv_oid,
        "user_id": ObjectId(current_user_id)
    })
    if not conversation_to_delete:
        return jsonify({"erro": "Conversa não encontrada ou acesso negado"}), 404

    # --- NOVA LÓGICA DE LIMPEZA DE ARQUIVOS ---
    # 2. Encontrar todos os documentos associados a esta conversa
    messages_in_conv = db.messages.find({"conversation_id": conv_oid})
    document_ids_to_delete = []
    for msg in messages_in_conv:
        if msg.get("generated_document_id"):
            document_ids_to_delete.append(msg.get("generated_document_id"))
        # Opcional: decidir se quer excluir também os arquivos que o usuário enviou
        if msg.get("input_document_id"):
            document_ids_to_delete.append(msg.get("input_document_id"))
    
    # 3. Excluir os documentos encontrados
    if document_ids_to_delete:
        # Pega apenas os IDs únicos para evitar tentar deletar o mesmo arquivo duas vezes
        unique_doc_ids = list(set(document_ids_to_delete))
        
        # Encontra os gridfs_file_ids antes de deletar os metadados
        docs_meta = db.documents.find({"_id": {"$in": unique_doc_ids}})
        gridfs_ids_to_delete = [doc.get("gridfs_file_id") for doc in docs_meta]
        
        # Deleta os metadados
        db.documents.delete_many({"_id": {"$in": unique_doc_ids}})
        
        # Deleta os arquivos no GridFS
        for gridfs_id in gridfs_ids_to_delete:
            if gridfs_id:
                fs.delete(gridfs_id)
    # --- FIM DA NOVA LÓGICA ---

    # 4. Excluir a conversa e as mensagens (como antes)
    db.messages.delete_many({"conversation_id": conv_oid})
    db.conversations.delete_one({"_id": conv_oid})

    return jsonify({"mensagem": "Conversa e todos os dados associados foram excluídos."}), 200

@chat_bp.route('/messages/<string:message_id>', methods=['DELETE'])
@jwt_required()
def delete_message(message_id):
    """Exclui uma única mensagem de uma conversa."""
    current_user_id = get_jwt_identity()
    db = get_db()

    try:
        msg_oid = ObjectId(message_id)
    except InvalidId:
        return jsonify({"erro": "ID de mensagem inválido"}), 400

    # Encontra e exclui a mensagem, garantindo que ela pertença ao usuário logado
    # Esta é uma verificação de segurança importante
    delete_result = db.messages.delete_one({
        "_id": msg_oid,
        "user_id": ObjectId(current_user_id)
    })

    if delete_result.deleted_count == 0:
        return jsonify({"erro": "Mensagem não encontrada ou acesso negado"}), 404

    return jsonify({"mensagem": "Mensagem excluída com sucesso."}), 200

@chat_bp.route('/conversations/<string:conversation_id>/rename', methods=['PUT'])
@jwt_required()
def rename_conversation(conversation_id):
    """Renomeia o título de uma conversa."""
    current_user_id = get_jwt_identity()
    data = request.get_json()
    new_title = data.get("new_title")

    if not new_title:
        return jsonify({"erro": "O campo 'new_title' é obrigatório"}), 400
    
    db = get_db()
    result = db.conversations.update_one(
        {"_id": ObjectId(conversation_id), "user_id": ObjectId(current_user_id)},
        {"$set": {"title": new_title, "last_updated_at": datetime.utcnow()}}
    )

    if result.matched_count == 0:
        return jsonify({"erro": "Conversa não encontrada ou acesso negado"}), 404
        
    return jsonify({"mensagem": "Conversa renomeada com sucesso."})

@chat_bp.route('/messages/<string:message_id>/edit', methods=['PUT'])
@jwt_required()
def edit_message(message_id):
    """Edita o conteúdo de uma mensagem de usuário e refaz a conversa a partir dali."""
    current_user_id = get_jwt_identity()
    data = request.get_json()
    new_content = data.get("new_content")

    if not new_content:
        return jsonify({"erro": "O campo 'new_content' é obrigatório"}), 400

    db = get_db()
    msg_oid = ObjectId(message_id)

    # 1. Encontrar a mensagem original para garantir a permissão
    original_message = db.messages.find_one({"_id": msg_oid, "user_id": ObjectId(current_user_id)})
    if not original_message or original_message.get("role") != "user":
        return jsonify({"erro": "Mensagem não encontrada, não pertence ao usuário ou não é um prompt de usuário."}), 404
    
    # 2. Excluir todas as mensagens que vieram DEPOIS desta
    db.messages.delete_many({
        "conversation_id": original_message["conversation_id"],
        "timestamp": {"$gt": original_message["timestamp"]}
    })

    # 3. Atualizar a mensagem atual
    db.messages.update_one({"_id": msg_oid}, {"$set": {"content": new_content}})

    # 4. Acionar a IA novamente com o ID desta mensagem atualizada
    processar_solicitacao_ia(message_id)

    return jsonify({"mensagem": "Mensagem editada. A conversa está sendo reprocessada."}), 200

@chat_bp.route('/messages/<string:message_id>/regenerate', methods=['POST'])
@jwt_required()
def regenerate_message(message_id):
    """Refaz a resposta da IA para uma mensagem de usuário."""
    # A lógica é muito similar à de edição, mas sem mudar o conteúdo
    current_user_id = get_jwt_identity()
    db = get_db()
    msg_oid = ObjectId(message_id)

    original_message = db.messages.find_one({"_id": msg_oid, "user_id": ObjectId(current_user_id)})
    if not original_message or original_message.get("role") != "user":
        return jsonify({"erro": "Mensagem não encontrada, não pertence ao usuário ou não é um prompt de usuário."}), 404

    db.messages.delete_many({
        "conversation_id": original_message["conversation_id"],
        "timestamp": {"$gt": original_message["timestamp"]}
    })
    
    processar_solicitacao_ia(message_id)
    
    return jsonify({"mensagem": "A resposta está sendo regenerada."}), 200