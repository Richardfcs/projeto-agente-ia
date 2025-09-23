# Arquivo: /src/api/auth/routes.py

from flask import Blueprint, request, jsonify
from passlib.context import CryptContext
from flask_jwt_extended import create_access_token, jwt_required, get_jwt_identity
from bson import ObjectId
from src.db.mongo import get_db

# Cria o Blueprint para as rotas de autenticação
auth_bp = Blueprint('auth_bp', __name__)

# Configura o passlib para hashing de senhas
# Usaremos bcrypt, que é o padrão da indústria
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

    # Verifica se o usuário já existe
    if users_collection.find_one({"username": username}):
        return jsonify({"erro": "Este nome de usuário já está em uso"}), 409 # 409 Conflict

    # Cria o hash da senha antes de salvar
    hashed_password = pwd_context.hash(password)
    
    # Insere o novo usuário no banco de dados
    user_data = {
        "username": username,
        "hashed_password": hashed_password
    }
    users_collection.insert_one(user_data)

    return jsonify({"mensagem": "Usuário registrado com sucesso!"}), 201 # 201 Created

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

    # Verifica se o usuário existe e se a senha está correta
    if not user or not pwd_context.verify(password, user['hashed_password']):
        return jsonify({"erro": "Credenciais inválidas"}), 401 # 401 Unauthorized

    # Cria o token de acesso. A "identidade" pode ser qualquer dado único do usuário.
    # Usar o _id (convertido para string) é uma ótima prática.
    access_token = create_access_token(identity=str(user['_id']))
    
    return jsonify(access_token=access_token)

@auth_bp.route('/profile', methods=['GET'])
@jwt_required() # Este decorador protege a rota!
def profile():
    """Endpoint protegido que retorna os dados do usuário logado."""
    
    # Pega a identidade do usuário a partir do token JWT
    current_user_id = get_jwt_identity()
    
    db = get_db()
    users_collection = db.users
    
    # Busca o usuário no banco de dados pelo seu ID
    user = users_collection.find_one({"_id": ObjectId(current_user_id)})
    
    if not user:
        return jsonify({"erro": "Usuário não encontrado"}), 404

    # Retorna os dados do usuário (NUNCA retorne a senha!)
    return jsonify({
        "id": str(user['_id']),
        "username": user['username']
    })