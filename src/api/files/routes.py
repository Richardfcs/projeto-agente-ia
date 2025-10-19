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

@files_bp.route('/documents/<string:document_id>/download', methods=['GET'])
@jwt_required()
def download_document_by_id(document_id):
    """
    Permite o download de um documento usando o ID do metadado (_id),
    abstraindo o ID do GridFS para o frontend. Esta é a rota preferencial
    para ser usada pela interface do usuário.
    """
    current_user_id = get_jwt_identity()
    db = get_db()
    fs = get_gridfs()

    try:
        # 1. Valida o ID do documento recebido na URL
        doc_oid = ObjectId(document_id)
    except InvalidId:
        return jsonify({"erro": "ID de documento inválido"}), 400

    # 2. Encontra o metadado do documento e, crucialmente, verifica se ele pertence ao usuário logado.
    #    Esta é a verificação de segurança mais importante.
    doc_meta = db.documents.find_one({
        "_id": doc_oid, 
        "owner_id": ObjectId(current_user_id)
    })

    if not doc_meta:
        # Se não encontrar, retorna 404, seja porque o documento não existe ou por falta de permissão.
        return jsonify({"erro": "Documento não encontrado ou acesso negado"}), 404

    # 3. Obtém o ID do GridFS a partir do metadado encontrado
    gridfs_id = doc_meta.get("gridfs_file_id")
    if not gridfs_id:
        # Caso de segurança para dados inconsistentes no banco
        return jsonify({"erro": "Metadado do documento está corrompido (sem ID de arquivo)"}), 500

    try:
        # 4. Usa o ID do GridFS para buscar o arquivo real no sistema de armazenamento
        gridfs_file = fs.get(gridfs_id)
        
        # 5. Envia o arquivo como um stream para o cliente, forçando o download.
        #    Passar o objeto 'gridfs_file' diretamente é a forma mais eficiente em termos de memória.
        return send_file(
            gridfs_file,
            download_name=gridfs_file.filename, # Garante o nome original do arquivo
            as_attachment=True # Força o navegador a abrir a caixa de diálogo "Salvar como..."
        )
    except NoFile:
        return jsonify({"erro": "Arquivo não encontrado no sistema de armazenamento (GridFS)"}), 404

@files_bp.route('/documents/<string:document_id>/metadata', methods=['GET'])
@jwt_required()
def get_document_metadata(document_id):
    """
    Retorna apenas os metadados de um documento específico.
    """
    current_user_id = get_jwt_identity()
    db = get_db()

    try:
        doc_oid = ObjectId(document_id)
    except InvalidId:
        return jsonify({"erro": "ID de documento inválido"}), 400

    doc_meta = db.documents.find_one({
        "_id": doc_oid,
        "owner_id": ObjectId(current_user_id)
    })

    if not doc_meta:
        return jsonify({"erro": "Documento não encontrado ou acesso negado"}), 404
    
    # Prepara os dados para serem enviados como JSON
    doc_meta['_id'] = str(doc_meta['_id'])
    doc_meta['owner_id'] = str(doc_meta['owner_id'])
    doc_meta['gridfs_file_id'] = str(doc_meta['gridfs_file_id'])
    if 'created_at' in doc_meta:
        doc_meta['created_at'] = doc_meta['created_at'].isoformat()
        
    return jsonify(doc_meta)

@files_bp.route('/documents', methods=['GET'])
@jwt_required()
def list_documents():
    current_user_id = get_jwt_identity()
    db = get_db()
    
    # --- INÍCIO DA LÓGICA DE PAGINAÇÃO ---
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return jsonify({"erro": "Parâmetros 'page' e 'limit' devem ser números inteiros"}), 400

    if page < 1 or limit < 1:
        return jsonify({"erro": "Parâmetros 'page' e 'limit' devem ser maiores que zero"}), 400
        
    # Calcula quantos documentos pular
    skip = (page - 1) * limit
    
    # Define o filtro da busca
    query_filter = {"owner_id": ObjectId(current_user_id)}
    
    # Conta o número total de documentos que correspondem ao filtro (essencial para o frontend)
    total_documents = db.documents.count_documents(query_filter)
    
    # Busca a página de documentos
    docs_cursor = db.documents.find(query_filter).sort("created_at", -1).skip(skip).limit(limit)
    # --- FIM DA LÓGICA DE PAGINAÇÃO ---

    documents_list = []
    for doc in docs_cursor:
        doc['_id'] = str(doc['_id'])
        doc['owner_id'] = str(doc['owner_id'])
        doc['gridfs_file_id'] = str(doc['gridfs_file_id'])
        documents_list.append(doc)
        
    # --- NOVA ESTRUTURA DE RESPOSTA ---
    # A resposta agora é um objeto que contém os dados e as informações de paginação
    return jsonify({
        "data": documents_list,
        "pagination": {
            "total_items": total_documents,
            "total_pages": (total_documents + limit - 1) // limit, # Cálculo para arredondar para cima
            "current_page": page,
            "items_per_page": limit
        }
    })

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
    
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return jsonify({"erro": "Parâmetros 'page' e 'limit' devem ser números inteiros"}), 400
    
    if page < 1 or limit < 1:
        return jsonify({"erro": "Parâmetros 'page' e 'limit' devem ser maiores que zero"}), 400

    skip = (page - 1) * limit

    # --- INÍCIO DA MUDANÇA ---
    # Removido: search_regex = re.compile(f".*{re.escape(query)}.*", re.IGNORECASE)
    
    # O filtro agora usa o operador $text para uma busca otimizada.
    # O $search aceita a string de busca diretamente.
    query_filter = {
        "owner_id": ObjectId(current_user_id),
        "$text": {
            "$search": query
        }
    }
    # --- FIM DA MUDANÇA ---
    
    # Conta o total de documentos que correspondem à BUSCA
    total_documents = db.documents.count_documents(query_filter)

    # A busca continua a mesma, mas agora usa o novo 'query_filter' otimizado
    docs_cursor = db.documents.find(query_filter).sort("created_at", -1).skip(skip).limit(limit)

    documents_list = []
    for doc in docs_cursor:
        doc['_id'] = str(doc['_id'])
        doc['owner_id'] = str(doc['owner_id'])
        doc['gridfs_file_id'] = str(doc['gridfs_file_id'])
        documents_list.append(doc)
        
    # --- NOVA ESTRUTURA DE RESPOSTA ---
    return jsonify({
        "data": documents_list,
        "pagination": {
            "total_items": total_documents,
            "total_pages": (total_documents + limit - 1) // limit,
            "current_page": page,
            "items_per_page": limit
        }
    })

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
    
    # --- INÍCIO DA LÓGICA DE PAGINAÇÃO ---
    try:
        page = int(request.args.get('page', 1))
        limit = int(request.args.get('limit', 20))
    except ValueError:
        return jsonify({"erro": "Parâmetros 'page' e 'limit' devem ser números inteiros"}), 400
        
    if page < 1 or limit < 1:
        return jsonify({"erro": "Parâmetros 'page' e 'limit' devem ser maiores que zero"}), 400

    skip = (page - 1) * limit
    
    query_filter = {} # Sem filtro específico para templates
    
    total_templates = db.templates.count_documents(query_filter)
    
    templates_cursor = db.templates.find(query_filter).sort("filename", 1).skip(skip).limit(limit)
    # --- FIM DA LÓGICA DE PAGINAÇÃO ---

    templates_list = []
    for t in templates_cursor:
        t['_id'] = str(t['_id'])
        t['uploaded_by'] = str(t['uploaded_by'])
        t['gridfs_file_id'] = str(t['gridfs_file_id'])
        templates_list.append(t)
        
    # --- NOVA ESTRUTURA DE RESPOSTA ---
    return jsonify({
        "data": templates_list,
        "pagination": {
            "total_items": total_templates,
            "total_pages": (total_templates + limit - 1) // limit,
            "current_page": page,
            "items_per_page": limit
        }
    })