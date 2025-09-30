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
    current_user_id = get_jwt_identity()

    if 'file' not in request.files:
        return jsonify({"erro": "Nenhum arquivo enviado"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"erro": "Nome de arquivo vazio"}), 400
    
    ## SUGESTÃO (Segurança): Considere validar a extensão ou o tipo MIME do arquivo
    ## para permitir apenas formatos esperados (ex: 'pdf', 'docx', 'png'), 
    ## prevenindo o upload de arquivos potencialmente maliciosos.

    db = get_db()
    fs = get_gridfs()
    
    file_id = fs.put(file, filename=file.filename)

    document_meta = {
        "filename": file.filename,
        "gridfs_file_id": file_id,
        "owner_id": ObjectId(current_user_id),
        "created_at": datetime.utcnow()
    }
    result = db.documents.insert_one(document_meta)

    ## MELHORIA (Consistência da API): Retorne o objeto de metadado criado.
    # Isso fornece ao cliente o 'document_id' (_id) imediatamente, que é necessário
    # para as operações de renomear e excluir, evitando uma chamada extra.
    created_document = {
        "_id": str(result.inserted_id),
        "filename": file.filename,
        "gridfs_file_id": str(file_id),
        "owner_id": current_user_id,
        "created_at": document_meta["created_at"].isoformat()
    }

    return jsonify({
        "mensagem": "Documento enviado com sucesso!",
        "document": created_document
    }), 201

@files_bp.route('/templates/upload', methods=['POST'])
@jwt_required()
def upload_template():
    current_user_id = get_jwt_identity()
    # TODO: Adicionar verificação de role para admins.

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
    current_user_id = get_jwt_identity()
    db = get_db()
    fs = get_gridfs()

    try:
        oid = ObjectId(file_id)
    except InvalidId:
        return jsonify({"erro": "ID de arquivo inválido"}), 400

    doc_meta = db.documents.find_one({"gridfs_file_id": oid, "owner_id": ObjectId(current_user_id)})
    template_meta = db.templates.find_one({"gridfs_file_id": oid})

    if not doc_meta and not template_meta:
        return jsonify({"erro": "Arquivo não encontrado ou acesso negado"}), 404

    try:
        gridfs_file = fs.get(oid)
        
        ## MELHORIA (Desempenho): Evite carregar o arquivo inteiro na memória.
        # O objeto 'gridfs_file' já é um stream. Passe-o diretamente para o send_file
        # para que o Flask faça o streaming do arquivo em pedaços (chunks).
        # Isso economiza muita memória em arquivos grandes.
        # Original: file_stream = io.BytesIO(gridfs_file.read())
        
        return send_file(
            gridfs_file,
            download_name=gridfs_file.filename,
            as_attachment=True
        )
    except NoFile:
        return jsonify({"erro": "Arquivo não encontrado no sistema de armazenamento"}), 404

@files_bp.route('/documents', methods=['GET'])
@jwt_required()
def list_documents():
    current_user_id = get_jwt_identity()
    db = get_db()
    
    ## SUGESTÃO (Escalabilidade): Implementar paginação para evitar sobrecarga.
    # Ex: page = request.args.get('page', 1, type=int)
    # Ex: limit = request.args.get('limit', 20, type=int)
    # Ex: .skip((page - 1) * limit).limit(limit)
    
    docs_cursor = db.documents.find(
        {"owner_id": ObjectId(current_user_id)}
    ).sort("created_at", -1)

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
    current_user_id = get_jwt_identity()
    db = get_db()
    fs = get_gridfs()

    try:
        doc_oid = ObjectId(document_id)
    except InvalidId:
        return jsonify({"erro": "ID de documento inválido"}), 400

    doc_meta = db.documents.find_one_and_delete({
        "_id": doc_oid,
        "owner_id": ObjectId(current_user_id)
    })

    if not doc_meta:
        return jsonify({"erro": "Documento não encontrado ou acesso negado"}), 404

    ## OBSERVAÇÃO (Robustez): Se a operação a seguir falhar, o arquivo
    ## no GridFS ficará "órfão". Para sistemas críticos, considere adicionar
    ## um log de erro aqui para facilitar a limpeza posterior.
    gridfs_file_id = doc_meta.get("gridfs_file_id")
    if gridfs_file_id:
        fs.delete(gridfs_file_id)
        
    return jsonify({"mensagem": f"Documento '{doc_meta.get('filename')}' excluído com sucesso."}), 200

@files_bp.route('/documents/search', methods=['GET'])
@jwt_required()
def search_documents():
    current_user_id = get_jwt_identity()
    query = request.args.get('q', '')

    if not query:
        return jsonify({"erro": "Parâmetro de busca 'q' é obrigatório"}), 400

    db = get_db()
    
    ## MELHORIA (Desempenho): A busca com regex pode ser lenta.
    # Para uma melhor performance, crie um índice de texto no campo 'filename' no MongoDB
    # e use a busca por texto.
    # 1. Crie o índice (apenas uma vez): `db.documents.create_index([("filename", "text")])`
    # 2. Mude a query para: `{"owner_id": ObjectId(current_user_id), "$text": {"$search": query}}`
    # A query atual funciona, mas não escala bem.
    search_regex = re.compile(f".*{re.escape(query)}.*", re.IGNORECASE)
    
    ## SUGESTÃO (Escalabilidade): Adicione paginação aqui também.
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

@files_bp.route('/documents/<string:document_id>/rename', methods=['PUT'])
@jwt_required()
def rename_document(document_id):
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

    doc_meta = db.documents.find_one({"_id": doc_oid, "owner_id": ObjectId(current_user_id)})
    if not doc_meta:
        return jsonify({"erro": "Documento não encontrado ou acesso negado"}), 404

    # Atualiza o metadado na coleção 'documents'
    db.documents.update_one({"_id": doc_oid}, {"$set": {"filename": new_filename}})
    
    ## OBSERVAÇÃO (Robustez): Assim como no delete, esta é uma segunda operação de escrita.
    ## Se ela falhar, os nomes ficarão inconsistentes entre a sua coleção e a do GridFS.
    gridfs_file_id = doc_meta.get("gridfs_file_id")
    if gridfs_file_id:
        db.fs.files.update_one(
            {"_id": gridfs_file_id},
            {"$set": {"filename": new_filename}}
        )
    
    return jsonify({"mensagem": "Documento renomeado com sucesso."})

@files_bp.route('/templates', methods=['GET'])
@jwt_required()
def list_templates():
    db = get_db()
    templates_cursor = db.templates.find({}).sort("filename", 1)
    
    templates_list = []
    for t in templates_cursor:
        t['_id'] = str(t['_id'])
        t['uploaded_by'] = str(t['uploaded_by'])
        t['gridfs_file_id'] = str(t['gridfs_file_id'])
        templates_list.append(t)
        
    return jsonify(templates_list)