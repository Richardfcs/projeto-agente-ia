# Arquivo: src/api/chat/routes.py

from flask import Blueprint, request, jsonify

# Cria o Blueprint para as rotas do chat
chat_bp = Blueprint('chat_bp', __name__)

@chat_bp.route('/gerar', methods=['POST'])
def gerar_relatorio():
    data = request.get_json()
    if not data or 'prompt' not in data:
        return jsonify({"erro": "O campo 'prompt' é obrigatório"}), 400

    prompt = data['prompt']
    
    # TODO: Implementar a lógica da Seção 4
    # 1. Criar um novo documento na coleção `conversations` e `messages`.
    # 2. Adicionar a tarefa de processamento à fila do Celery.
    # 3. Retornar o ID da conversa/mensagem para o frontend.
    
    return jsonify({
        "mensagem": "Sua solicitação foi recebida e está sendo processada.",
        "conversation_id": "id_da_conversa_a_ser_criado" # Placeholder
    }), 202 # 202 Accepted

@chat_bp.route('/download/<file_id>', methods=['GET'])
def download_file(file_id):
    # TODO: Implementar a lógica da Seção 3
    # 1. Buscar o arquivo no GridFS usando o file_id.
    # 2. Retornar o arquivo para o usuário com `send_file`.
    return f"Lógica de download para o arquivo {file_id} a ser implementada."