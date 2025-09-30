# Arquivo: /src/api/auth/routes.py

import re
from datetime import datetime
from flask import Blueprint, request, jsonify
from passlib.context import CryptContext
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from bson import ObjectId
from src.db.mongo import get_db

auth_bp = Blueprint('auth_bp', __name__)

# Configura o passlib para hashing de senhas (sem alterações aqui)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Função auxiliar para validar o formato do email
def is_valid_email(email):
    """Verifica se o formato do email é válido usando uma expressão regular simples."""
    regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(regex, email) is not None

@auth_bp.route('/register', methods=['POST'])
def register():
    """Endpoint para registrar um novo usuário com nome completo, email e senha."""
    data = request.get_json()
    
    # MUDANÇA: Obter os novos campos do JSON
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    # MUDANÇA: Validação dos novos campos
    if not all([name, email, password]):
        return jsonify({"erro": "Nome completo, email e senha são obrigatórios"}), 400

    if not is_valid_email(email):
        return jsonify({"erro": "Formato de email inválido"}), 400

    db = get_db()
    users_collection = db.users

    # MUDANÇA: Verificar a existência do usuário pelo email
    if users_collection.find_one({"email": email}):
        return jsonify({"erro": "Este email já está em uso"}), 409

    hashed_password = pwd_context.hash(password)
    
    # MUDANÇA: Salvar a nova estrutura de dados do usuário
    user_data = {
        "name": name,
        "email": email.lower(), # Salvar email em minúsculas para consistência
        "hashed_password": hashed_password,
        "created_at": datetime.utcnow()
    }
    users_collection.insert_one(user_data)

    return jsonify({"mensagem": "Usuário registrado com sucesso!"}), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    """Endpoint para autenticar um usuário com email e senha."""
    data = request.get_json()
    
    # MUDANÇA: Usar email em vez de username
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"erro": "Email e senha são obrigatórios"}), 400

    db = get_db()
    users_collection = db.users
    
    # MUDANÇA: Encontrar o usuário pelo email
    user = users_collection.find_one({"email": email.lower()})

    # A lógica de verificação da senha permanece a mesma
    if not user or not pwd_context.verify(password, user['hashed_password']):
        return jsonify({"erro": "Credenciais inválidas"}), 401

    # A identidade no token continua sendo o _id, o que é perfeito e não quebra outras partes do sistema.
    access_token = create_access_token(identity=str(user['_id']))
    
    return jsonify(access_token=access_token)

@auth_bp.route('/profile', methods=['GET'])
@jwt_required()
def profile():
    """Endpoint protegido que retorna os dados do usuário logado."""
    current_user_id = get_jwt_identity()
    db = get_db()
    
    # A busca pelo _id continua funcionando perfeitamente
    user = db.users.find_one({"_id": ObjectId(current_user_id)})
    
    if not user:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    # MUDANÇA: Retornar os novos campos do perfil do usuário
    # Importante: NUNCA retorne a senha, mesmo que hasheada.
    return jsonify({
        "id": str(user['_id']),
        "name": user.get('name'),
        "email": user.get('email')
    })