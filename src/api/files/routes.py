# Arquivo: /src/api/files/routes.py

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime
from gridfs.errors import NoFile
from src.db.mongo import get_db, get_gridfs
import io
import re

# Cria o Blueprint para as rotas de arquivos
files_bp = Blueprint('files_bp', __name__)

@files_bp.route('/documents/upload', methods=['POST'])
@jwt_required()
def upload_document():
    """Endpoint para um usuário logado fazer upload de um documento."""
    current_user_id = get_jwt_identity()

    if 'file' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"erro": "Nome de arquivo vazio"}), 400

    db = get_db()
    fs = get_gridfs()
    
    # Salva o arquivo no GridFS e obtém seu ID
    file_id = fs.put(file, filename=file.filename)

    # Cria o documento de metadados para associar o arquivo ao usuário
    document_meta = {
        "filename": file.filename,
        "gridfs_file_id": file_id,
        "owner_id": ObjectId(current_user_id),
        "created_at": datetime.utcnow()
    }
    db.documents.insert_one(document_meta)

    return jsonify({
        "mensagem": "Documento enviado com sucesso!",
        "file_id": str(file_id)
    }), 201

@files_bp.route('/templates/upload', methods=['POST'])
@jwt_required()
def upload_template():
    """Endpoint para fazer upload de um template (pode ser restrito a admins no futuro)."""
    current_user_id = get_jwt_identity()
    # TODO: No futuro, adicionar uma verificação de role para garantir que apenas admins possam fazer isso.

    if 'file' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"erro": "Nome de arquivo vazio"}), 400
        
    db = get_db()
    fs = get_gridfs()
    
    file_id = fs.put(file, filename=file.filename)
    
    template_meta = {
        "filename": file.filename,
        "gridfs_file_id": file_id,
        "uploaded_by": ObjectId(current_user_id),
        "created_at": datetime.utcnow()
    }
    db.templates.insert_one(template_meta)
    
    return jsonify({
        "mensagem": "Template enviado com sucesso!",
        "file_id": str(file_id)
    }), 201

@files_bp.route('/files/<string:file_id>', methods=['GET'])
@jwt_required()
def download_file(file_id):
    """Endpoint para baixar um arquivo do GridFS."""
    current_user_id = get_jwt_identity()
    db = get_db()
    fs = get_gridfs()

    try:
        oid = ObjectId(file_id)
    except InvalidId:
        return jsonify({"erro": "ID de arquivo inválido"}), 400

    # Lógica de Permissão:
    # 1. Verifica se o arquivo é um documento pertencente ao usuário.
    # 2. Se não for, verifica se é um template (que qualquer um pode baixar).
    # 3. Se não for nenhum dos dois, nega o acesso.
    
    doc_meta = db.documents.find_one({"gridfs_file_id": oid, "owner_id": ObjectId(current_user_id)})
    template_meta = db.templates.find_one({"gridfs_file_id": oid})

    if not doc_meta and not template_meta:
        return jsonify({"erro": "Arquivo não encontrado ou acesso negado"}), 404

    try:
        gridfs_file = fs.get(oid)
        # Usa BytesIO para carregar o arquivo em memória para o send_file
        file_stream = io.BytesIO(gridfs_file.read())
        
        return send_file(
            file_stream,
            download_name=gridfs_file.filename,
            as_attachment=True
        )
    except NoFile:
        return jsonify({"erro": "Arquivo não encontrado no sistema de armazenamento"}), 404

@files_bp.route('/documents', methods=['GET'])
@jwt_required()
def list_documents():
    """Lista todos os metadados dos documentos pertencentes ao usuário logado."""
    current_user_id = get_jwt_identity()
    db = get_db()
    
    docs_cursor = db.documents.find(
        {"owner_id": ObjectId(current_user_id)}
    ).sort("created_at", -1) # Ordena dos mais recentes para os mais antigos

    documents_list = []
    for doc in docs_cursor:
        doc['_id'] = str(doc['_id'])
        doc['owner_id'] = str(doc['owner_id'])
        doc['gridfs_file_id'] = str(doc['gridfs_file_id'])
        documents_list.append(doc)
        
    return jsonify(documents_list)

@files_bp.route('/documents/<string:document_id>', methods=['DELETE'])
@jwt_required()
def delete_document(document_id):
    """Exclui um documento e seu arquivo correspondente no GridFS."""
    current_user_id = get_jwt_identity()
    db = get_db()
    fs = get_gridfs()

    try:
        doc_oid = ObjectId(document_id)
    except InvalidId:
        return jsonify({"erro": "ID de documento inválido"}), 400

    # 1. Encontrar o metadado do documento para verificar a permissão e obter o ID do GridFS
    doc_meta = db.documents.find_one_and_delete({
        "_id": doc_oid,
        "owner_id": ObjectId(current_user_id)
    })

    if not doc_meta:
        # Se não encontrou, ou o documento não existe ou não pertence ao usuário
        return jsonify({"erro": "Documento não encontrado ou acesso negado"}), 404

    # 2. Se o metadado foi encontrado e excluído, exclui o arquivo no GridFS
    gridfs_file_id = doc_meta.get("gridfs_file_id")
    if gridfs_file_id:
        fs.delete(gridfs_file_id)
        
    return jsonify({"mensagem": f"Documento '{doc_meta.get('filename')}' excluído com sucesso."}), 200

@files_bp.route('/documents/search', methods=['GET'])
@jwt_required()
def search_documents():
    """Busca documentos do usuário por nome."""
    current_user_id = get_jwt_identity()
    query = request.args.get('q', '') # Pega o parâmetro 'q' da URL

    if not query:
        return jsonify({"erro": "Parâmetro de busca 'q' é obrigatório"}), 400

    db = get_db()
    # Usa regex para busca parcial, 'i' para ser case-insensitive
    search_regex = re.compile(f".*{re.escape(query)}.*", re.IGNORECASE)
    
    docs_cursor = db.documents.find({
        "owner_id": ObjectId(current_user_id),
        "filename": search_regex
    }).sort("created_at", -1)

    documents_list = []
    for doc in docs_cursor:
        doc['_id'] = str(doc['_id'])
        doc['owner_id'] = str(doc['owner_id'])
        doc['gridfs_file_id'] = str(doc['gridfs_file_id'])
        documents_list.append(doc)
        
    return jsonify(documents_list)

# --- NOVA ROTA DE RENOMEAR ---
@files_bp.route('/documents/<string:document_id>/rename', methods=['PUT'])
@jwt_required()
def rename_document(document_id):
    """Renomeia um documento existente."""
    current_user_id = get_jwt_identity()
    data = request.get_json()
    new_filename = data.get("new_filename")

    if not new_filename:
        return jsonify({"erro": "O campo 'new_filename' é obrigatório"}), 400

    db = get_db()
    
    try:
        doc_oid = ObjectId(document_id)
    except InvalidId:
        return jsonify({"erro": "ID de documento inválido"}), 400

    # 1. Encontra o nosso metadado para garantir a permissão
    doc_meta = db.documents.find_one({"_id": doc_oid, "owner_id": ObjectId(current_user_id)})
    if not doc_meta:
        return jsonify({"erro": "Documento não encontrado ou acesso negado"}), 404

    # 2. Renomeia o metadado na nossa coleção 'documents'
    db.documents.update_one({"_id": doc_oid}, {"$set": {"filename": new_filename}})
    
    # --- A MUDANÇA ESTÁ AQUI ---
    # 3. Renomeia o metadado na coleção 'fs.files' do GridFS
    # O GridFS armazena seus metadados em uma coleção chamada 'fs.files'.
    # Nós podemos atualizá-la diretamente.
    gridfs_file_id = doc_meta.get("gridfs_file_id")
    if gridfs_file_id:
        db.fs.files.update_one(
            {"_id": gridfs_file_id},
            {"$set": {"filename": new_filename}}
        )
    # --- FIM DA MUDANÇA ---
    
    return jsonify({"mensagem": "Documento renomeado com sucesso."})


# --- NOVA ROTA PARA LISTAR TEMPLATES ---
@files_bp.route('/templates', methods=['GET'])
@jwt_required()
def list_templates():
    """Lista todos os templates disponíveis no sistema."""
    db = get_db()
    templates_cursor = db.templates.find({}).sort("filename", 1)
    
    templates_list = []
    for t in templates_cursor:
        t['_id'] = str(t['_id'])
        t['uploaded_by'] = str(t['uploaded_by'])
        t['gridfs_file_id'] = str(t['gridfs_file_id'])
        templates_list.append(t)
        
    return jsonify(templates_list)