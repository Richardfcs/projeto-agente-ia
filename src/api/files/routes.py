# Arquivo: /src/api/files/routes.py

from flask import Blueprint, request, jsonify, send_file
from flask_jwt_extended import jwt_required, get_jwt_identity
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime
from gridfs.errors import NoFile
from src.db.mongo import get_db, get_gridfs
import io

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