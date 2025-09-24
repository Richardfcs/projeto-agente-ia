# Arquivo: /src/api/auth/routes.py

from flask import Blueprint, request, jsonify
from passlib.context import CryptContext
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from bson import ObjectId
from src.db.mongo import get_db
from datetime import datetime

# A linha mais importante: Cria a variável `auth_bp` que o __init__.py precisa importar.
auth_bp = Blueprint('auth_bp', __name__)

# Configura o passlib para hashing de senhas
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

@auth_bp.route('/register', methods=['POST'])
def register():
    """Endpoint para registrar um novo usuário."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400

    db = get_db()
    users_collection = db.users

    if users_collection.find_one({"username": username}):
        return jsonify({"erro": "Este nome de usuário já está em uso"}), 409

    hashed_password = pwd_context.hash(password)
    
    user_data = {
        "username": username,
        "hashed_password": hashed_password,
        "created_at": datetime.utcnow()
    }
    users_collection.insert_one(user_data)

    return jsonify({"mensagem": "Usuário registrado com sucesso!"}), 201

@auth_bp.route('/login', methods=['POST'])
def login():
    """Endpoint para autenticar um usuário e retornar um token JWT."""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({"erro": "Usuário e senha são obrigatórios"}), 400

    db = get_db()
    users_collection = db.users
    user = users_collection.find_one({"username": username})

    if not user or not pwd_context.verify(password, user['hashed_password']):
        return jsonify({"erro": "Credenciais inválidas"}), 401

    access_token = create_access_token(identity=str(user['_id']))
    
    return jsonify(access_token=access_token)

@auth_bp.route('/profile', methods=['GET'])
@jwt_required()
def profile():
    """Endpoint protegido que retorna os dados do usuário logado."""
    current_user_id = get_jwt_identity()
    db = get_db()
    users_collection = db.users
    
    user = users_collection.find_one({"_id": ObjectId(current_user_id)})
    
    if not user:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    return jsonify({
        "id": str(user['_id']),
        "username": user['username']
    })