# src/services/intelligent_router.py

from typing import List, Literal, Union
from pydantic import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI

from src.config import Config

# --- 1. Defina as "Ferramentas" que representam cada intenção ---
# Cada classe descreve um fluxo de trabalho para o LLM.

class FillTemplate(BaseModel):
    """Roteia para o fluxo de preenchimento de um template DOCX existente."""
    template_name: str = Field(description="O nome do arquivo do template. Ex: 'proposta.docx'.")
    topic: str = Field(description="O tópico principal ou o assunto do documento a ser preenchido.")

class CreateDocument(BaseModel):
    """Roteia para o fluxo de criação de um novo documento a partir do zero."""
    topic: str = Field(description="O tópico ou o conteúdo principal do documento a ser criado.")
    file_type: Literal["docx", "xlsx", "pdf"] = Field(description="O tipo de arquivo a ser criado. Use 'xlsx' para planilhas ou tabelas, 'docx' para relatórios ou textos, 'pdf' para documentos formais.")

class ReadDocument(BaseModel):
    """Roteia para o fluxo de leitura e análise de um documento anexado pelo usuário."""
    question: str = Field(description="A pergunta específica que o usuário tem sobre o conteúdo do documento.")

class GeneralChat(BaseModel):
    """Roteia para o fluxo de conversa geral para qualquer solicitação que não se encaixe nas outras ferramentas."""
    user_request: str = Field(description="A solicitação ou pergunta original do usuário.")


# --- 2. Crie o Roteador ---
# Esta classe vai amarrar tudo.

class IntelligentRouter:
    def __init__(self):
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-lite",
            google_api_key=Config.GOOGLE_API_KEY,
            temperature=0  # Temperatura zero para máxima previsibilidade no roteamento
        )
        
        # --- CORREÇÃO APLICADA AQUI ---
        # Em vez de with_structured_output, usamos .bind_tools() para permitir
        # que o LLM escolha entre MÚLTIPLAS ferramentas/esquemas.
        self.tools = [FillTemplate, CreateDocument, ReadDocument, GeneralChat]
        self.runnable = self.llm.bind_tools(
            tools=self.tools,
            # tool_choice="any" é o padrão, mas podemos ser explícitos
            # para garantir que ele sempre escolha uma das ferramentas.
            tool_choice="any" 
        )

    def route(self, prompt: str, conversation_history: List[dict], has_attachment: bool):
        """
        Analisa o prompt do usuário e o contexto para determinar a intenção correta.
        Retorna o nome da ferramenta escolhida e seus argumentos.
        """
        system_prompt = f"""Você é um roteador de IA especialista. Sua tarefa é analisar o prompt do usuário e o contexto da conversa e invocar a ferramenta mais adequada para atender à solicitação.

        Regras de Roteamento:
        1. Se o usuário mencionar explicitamente um 'template' e um nome de arquivo .docx, use a ferramenta 'FillTemplate'.
        2. Se o usuário pedir para 'criar', 'fazer', 'gerar' um 'relatório', 'planilha', 'documento', 'tabela' ou algo similar, use a ferramenta 'CreateDocument'. Seja inteligente ao determinar o 'file_type'. 'Planilha' ou 'tabela' deve ser 'xlsx'.
        3. Se houver um documento anexado (`has_attachment=True`) E o usuário estiver fazendo uma pergunta, use a ferramenta 'ReadDocument'.
        4. Para QUALQUER outra coisa (saudações, perguntas gerais, pedidos criativos, etc.), use a ferramenta 'GeneralChat'. Esta é a sua opção padrão.

        Contexto da Conversa:
        {conversation_history}

        Anexe um documento: {has_attachment}
        """
        
        # Invocamos o LLM com as ferramentas vinculadas
        ai_msg = self.runnable.invoke([
            ("system", system_prompt),
            ("human", prompt)
        ])
        
        # A resposta conterá uma lista de `tool_calls`. Pegamos a primeira.
        # Se não houver chamadas de ferramenta, algo deu errado, então usamos um fallback.
        if not ai_msg.tool_calls:
            # Fallback para GeneralChat se o LLM não escolher nenhuma ferramenta
            return "GeneralChat", {"user_request": prompt}

        first_tool_call = ai_msg.tool_calls[0]
        return first_tool_call['name'], first_tool_call['args']