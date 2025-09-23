# Arquivo: run.py

from dotenv import load_dotenv

# Carrega as variáveis do arquivo .env
load_dotenv()

from src import create_app

# Cria a instância da aplicação usando a factory
app = create_app()

if __name__ == '__main__':
    # Roda a aplicação. O modo debug é controlado pela variável FLASK_ENV no .env
    app.run(host='0.0.0.0', port=5000)