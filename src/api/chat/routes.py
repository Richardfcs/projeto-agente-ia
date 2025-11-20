# Arquivo: /src/api/chat/routes.py

from flask import Blueprint, request, jsonify
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime, timedelta
from src.db.mongo import get_db, get_gridfs
from src.tasks.ia_processor import processar_solicitacao_ia

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from src.tasks.llm_fallback import FallbackLLM
from src.config import Config

chat_bp = Blueprint('chat_bp', __name__)

# --- CONFIGURAÇÃO DE TÍTULO COM FALLBACK ---
try:
    title_generation_llm = FallbackLLM(temperature=0.3)
except Exception as e:
    # Se falhar, definimos como None para que possamos lidar com o erro graciosamente.
    title_generation_llm = None

def generate_conversation_title(first_prompt: str) -> str:
    """
    Usa um LLM para gerar um título curto e descritivo.
    """
    # Se o LLM não pôde ser inicializado, retorna um título de fallback.
    if not title_generation_llm:
        # Pega as primeiras 5 palavras do prompt como um fallback simples.
        return " ".join(first_prompt.split()[:5]) + "..."

    try:
        # Prompt otimizado para a tarefa de criar títulos.
        prompt = ChatPromptTemplate.from_template(
            """Sua tarefa é criar um título curto, conciso e descritivo (máximo 5 palavras) para uma conversa de chat que começa com a seguinte mensagem do usuário. 
            Responda APENAS com o título, sem aspas ou texto adicional.

            Exemplo 1:
            MENSAGEM: "Crie um relatório em docx sobre as vantagens e desvantagens da energia solar no Brasil."
            TÍTULO GERADO: Relatório sobre Energia Solar

            Exemplo 2:
            MENSAGEM: "Use o template 'proposta_comercial.docx' para a empresa InovaTech."
            TÍTULO GERADO: Proposta para InovaTech

            Exemplo 3:
            MENSAGEM: "Quais são as últimas tendências em inteligência artificial para 2025?"
            TÍTULO GERADO: Tendências em IA para 2025

            Exemplo 4:
            MENSAGEM: "O que você faz?"
            TÍTULO GERADO: Sobre Minhas Funcionalidades

            Exemplo 5:
            MENSAGEM: "Quais são os templates disponíveis?"
            TÍTULO GERADO: Templates Disponíveis

            MENSAGEM DO USUÁRIO: "{prompt}"
            """
        )
        
        chain = prompt | title_generation_llm | StrOutputParser()
        
        # Invoca a cadeia e limpa a resposta.
        title = chain.invoke({"prompt": first_prompt}).strip().strip('"')
        
        # Garante que o título não seja excessivamente longo.
        if len(title) > 70:
            title = title[:67] + "..."

        return title if title else "Novo Chat"
        
    except Exception:
        # Em caso de qualquer erro com a IA, retorna um título de fallback.
        return " ".join(first_prompt.split()[:5]) + "..."

# --- NOVA ROTA PARA CORRIGIR O BUG DO UPLOAD ---
@chat_bp.route('/conversations/init', methods=['POST'])
@jwt_required()
def init_conversation():
    """
    Cria uma conversa vazia (rascunho) e retorna o ID imediatamente.
    Essencial para permitir upload de arquivos antes da primeira mensagem.
    """
    current_user_id = get_jwt_identity()
    db = get_db()

    new_conv = {
        "user_id": ObjectId(current_user_id),
        "title": "Nova Conversa", # Título provisório
        "created_at": datetime.utcnow(),
        "last_updated_at": datetime.utcnow(),
        "is_empty": True # Marca como rascunho
    }
    result = db.conversations.insert_one(new_conv)
    
    return jsonify({
        "conversation_id": str(result.inserted_id),
        "title": "Nova Conversa"
    }), 201

@chat_bp.route('/conversations', methods=['POST'])
@jwt_required()
def send_message():
    """
    Envia mensagem. Lida com a geração de título na PRIMEIRA mensagem real,
    seja em um novo chat direto ou em um chat iniciado via /init.
    """
    current_user_id = get_jwt_identity()
    data = request.get_json()

    # Pega os dados do corpo da requisição
    prompt = data.get('prompt')
    conversation_id_str = data.get('conversation_id') 
    input_document_id_str = data.get('input_document_id')

    if not prompt:
        return jsonify({"erro": "O campo 'prompt' é obrigatório"}), 400

    db = get_db()
    new_title_generated = None
    
    # 1. Resolução do ID da Conversa
    if not conversation_id_str:
        # Fallback: Criação direta (sem chamar /init antes)
        new_conv = {
            "user_id": ObjectId(current_user_id),
            "title": "Nova Conversa",
            "created_at": datetime.utcnow(),
            "last_updated_at": datetime.utcnow(),
            "is_empty": True
        }
        result = db.conversations.insert_one(new_conv)
        conversation_id = result.inserted_id
    else:
        try:
            conversation_id = ObjectId(conversation_id_str)
            conv = db.conversations.find_one({
                "_id": conversation_id, 
                "user_id": ObjectId(current_user_id)
            })
            if not conv:
                return jsonify({"erro": "Conversa não encontrada ou acesso negado"}), 404
        except InvalidId:
            return jsonify({"erro": "ID de conversa inválido"}), 400

    # 2. Lógica de Título para Primeira Mensagem
    # Verifica se é a primeira mensagem para gerar o título definitivo
    msg_count = db.messages.count_documents({"conversation_id": conversation_id})
    
    if msg_count == 0:
        new_title_generated = generate_conversation_title(prompt)
        
        db.conversations.update_one(
            {"_id": conversation_id},
            {
                "$set": {
                    "title": new_title_generated,
                    "is_empty": False, # Remove flag de rascunho
                    "last_updated_at": datetime.utcnow()
                }
            }
        )

    # 3. Criação da Mensagem
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
                return jsonify({"erro": "Documento anexado não encontrado"}), 404
            user_message["input_document_id"] = doc_id
        except InvalidId:
            return jsonify({"erro": "ID de documento inválido"}), 400

    # Insere a mensagem final no banco de dados
    msg_result = db.messages.insert_one(user_message)

    # 4. Processamento IA
    resultado = processar_solicitacao_ia(str(msg_result.inserted_id))

    # Monta resposta
    response_data = {
        "mensagem": "Sua solicitação foi processada com sucesso.",
        "conversation_id": str(conversation_id),
        "message_id": str(msg_result.inserted_id)
    }
    
    if new_title_generated:
        response_data["new_title"] = new_title_generated

    if resultado == "Sucesso":
        return jsonify(response_data), 201
    else:
        return jsonify({"erro": "Ocorreu um problema no servidor ao processar sua solicitação."}), 500

@chat_bp.route('/conversations', methods=['GET'])
@jwt_required()
def get_conversations():
    """Lista conversas e limpa rascunhos vazios antigos."""
    current_user_id = get_jwt_identity()
    db = get_db()
    
    # Limpeza de rascunhos com mais de 1 hora
    one_hour_ago = datetime.utcnow() - timedelta(hours=1)
    db.conversations.delete_many({
        "user_id": ObjectId(current_user_id),
        "is_empty": True,
        "created_at": {"$lt": one_hour_ago}
    })
    
    convs_cursor = db.conversations.find(
        {
            "user_id": ObjectId(current_user_id),
            "is_empty": {"$ne": True} # Não lista rascunhos vazios
        }
    ).sort("last_updated_at", -1)
    
    conversations = []
    for conv in convs_cursor:
        conv['_id'] = str(conv['_id'])
        conv['user_id'] = str(conv['user_id'])
        conv.pop('is_empty', None)
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
    
    # Verifica se a conversa existe e pertence ao usuário logado
    conv = db.conversations.find_one({
        "_id": conversation_id, 
        "user_id": ObjectId(current_user_id)
    })
    if not conv:
        return jsonify({"erro": "Conversa não encontrada ou acesso negado"}), 404
    
    # Busca todas as mensagens da conversa, ordenadas pela data de criação
    msgs_cursor = db.messages.find(
        {"conversation_id": conversation_id}
    ).sort("timestamp", 1)
    
    messages = []
    for msg in msgs_cursor:
        # Itera sobre todas as chaves e valores do documento da mensagem
        for key, value in msg.items():
            # Se o valor for do tipo ObjectId, converte para string
            if isinstance(value, ObjectId):
                msg[key] = str(value)
        
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