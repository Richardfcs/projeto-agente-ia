# Projeto Agente de IA - Backend

Bem-vindo ao backend do projeto Agente de IA! Esta aplicação, construída com **Flask**, serve como o cérebro para um assistente de IA avançado. Ela utiliza **MongoDB** para um armazenamento de dados flexível, **GridFS** para lidar com arquivos grandes e o **CrewAI** para orquestrar uma equipe de agentes inteligentes alimentados pela API do **Google Gemini**.

O sistema é capaz de manter conversas contextuais, interagir com documentos (`.docx`, `.xlsx`), preencher templates complexos e gerenciar todos os dados do usuário de forma segura.

## ✨ Arquitetura e Funcionalidades

-   **API RESTful Completa:** Endpoints para autenticação, gerenciamento de conversas, upload/download de arquivos e muito mais.
-   **Autenticação Segura:** Sistema de registro e login com senhas criptografadas e autenticação baseada em **JWT (JSON Web Tokens)**.
-   **Arquitetura de Agentes (CrewAI):**
    -   **Gerente de Projetos:** Um agente de alto nível que analisa as solicitações do usuário.
    -   **Especialistas:** Agentes focados em tarefas específicas, como manipular documentos, responder perguntas ou lidar com erros.
    -   **Ferramentas Inteligentes:** Os agentes utilizam ferramentas personalizadas para interagir com o banco de dados e gerar documentos dinamicamente.
-   **Armazenamento Escalável:** Uso do **MongoDB Atlas** e **GridFS** para garantir performance e capacidade de armazenamento para um grande volume de dados e arquivos.
-   **Documentação Interativa:** Uma interface **Swagger UI** integrada para fácil exploração e teste de todos os endpoints da API.

## 📋 Pré-requisitos

Antes de começar, garanta que você tenha os seguintes softwares instalados:

-   **Python** (versão 3.10 ou superior)
-   **Pip** e **Venv** (geralmente inclusos no Python)
-   **Git** (para clonar o repositório)
-   Uma conta no [MongoDB Atlas](https://www.mongodb.com/cloud/atlas) para obter a string de conexão de um cluster gratuito (M0).

## ⚙️ Instalação e Configuração Local

Siga os passos abaixo para ter o ambiente de desenvolvimento rodando em sua máquina.

### 1. Clonar o Repositório

```bash
git clone https://sua-url-do-repositorio.git
cd projeto-agente-ia
```

### 2. Criar e Ativar o Ambiente Virtual

É uma boa prática isolar as dependências do projeto.

**Para macOS / Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
```
**Para Windows:**
```bash
python -m venv venv
.\venv\Scripts\activate
```

### 3. Instalar as Dependências

Com o ambiente virtual ativado (`(venv)` aparecerá no seu terminal), instale todas as bibliotecas necessárias:

```bash
pip install -r requirements.txt
```

### 4. Configurar as Variáveis de Ambiente

Este é o passo mais importante. As chaves de API e outras configurações são gerenciadas através de um arquivo `.env`.

a. **Crie um arquivo chamado `.env`** na raiz do projeto.

b. Copie o conteúdo abaixo para dentro do seu arquivo `.env` e **preencha com suas próprias chaves e configurações**:

```
# Arquivo: .env

# Configuração da Aplicação Flask
FLASK_ENV=development
SECRET_KEY='gere-uma-chave-secreta-forte-aqui'

# Conexão com o Banco de Dados MongoDB
# IMPORTANTE: Substitua <password> pela sua senha e adicione o nome do DB antes do '?'
MONGO_URI='mongodb+srv://<username>:<password>@seu-cluster.mongodb.net/agente_ia_db?retryWrites=true&w=majority'
MONGO_DB_NAME='agente_ia_db'

# Chave da API do Google Gemini
GOOGLE_API_KEY='sua-chave-da-api-do-google-aqui'
GEMINI_API_KEY='a-mesma-chave-acima'
```

🚨 **IMPORTANTE:** O arquivo `.env` contém informações sensíveis. Ele já está no `.gitignore` e **NUNCA** deve ser enviado para o repositório no GitHub.

## ▶️ Executando a Aplicação

Com a configuração concluída, iniciar o servidor é muito simples.

No seu terminal (com o `venv` ativado), execute:

```bash
python run.py
```

O servidor estará rodando e acessível em `http://127.0.0.1:5000`.

## 📚 Documentação da API (Swagger UI)

Uma vez que o servidor esteja rodando, você pode acessar a documentação interativa da API no seu navegador.

**URL da Documentação:** **`http://127.0.0.1:5000/api/docs`**

Nesta página, você pode:
-   Visualizar todos os endpoints disponíveis.
-   Ver os detalhes de cada rota (parâmetros, corpo da requisição, respostas).
-   **Testar a API diretamente do navegador** usando a funcionalidade "Try it out".

## ✅ Testando a Aplicação

Para testar o fluxo completo:
1.  Use a rota `POST /api/auth/register` para criar um usuário.
2.  Use a rota `POST /api/auth/login` para obter um `access_token`.
3.  Clique no botão **"Authorize"** no topo da página do Swagger e cole seu token no formato `Bearer <seu_token>`.
4.  Agora você pode testar os endpoints protegidos, como o `POST /api/chat/conversations`.
---
*Este projeto foi desenvolvido pelo Squad 42.*