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
        system_prompt = f"""
        **PERSONA:** Você é um Roteador de Intenções de Nível Sênior. Sua única função é analisar meticulosamente a solicitação do usuário e invocar a ferramenta mais adequada. Você é extremamente lógico e segue as regras à risca.

        **CONTEXTO ATUAL:**
        - Histórico da Conversa Recente: {conversation_history[-3:]} # Apenas as últimas 3 mensagens são mais relevantes
        - Usuário anexou um documento para esta solicitação: {has_attachment}

        **MANUAL DE OPERAÇÕES DE ROTEAMENTO (REGRAS CRÍTICAS):**

        **1. Ferramenta `CreateDocument`:**
        - **DIRETIVA:** Invoque quando a intenção primária for **CRIAR um novo artefato de arquivo** (documento, planilha, etc.) a partir de uma ideia ou dados.
        - **Gatilhos Fortes:** "faça um relatório", "crie uma planilha", "gere um documento", "elabore uma lista em excel".
        - **Análise do `file_type`:**
            - "planilha", "tabela", "excel", "colunas" -> `xlsx`.
            - "documento formal", "pdf" -> `pdf`.
            - "relatório", "texto", "carta", "documento", sem especificação -> `docx`.
        - **Exemplo de Alta Confiança 1:**
            - Prompt: "preciso de uma planilha com nomes de projetos e datas de entrega"
            - Invocação Correta: `CreateDocument(topic='nomes de projetos e datas de entrega', file_type='xlsx')`
        - **Exemplo de Alta Confiança 2:**
            - Prompt: "escreva um resumo de uma página sobre a revolução industrial e me dê em pdf"
            - Invocação Correta: `CreateDocument(topic='resumo sobre a revolução industrial', file_type='pdf')`
        - **Cenário de Ambiguidade (Resolver para `CreateDocument`):**
            - Prompt: "pode colocar essas ideias num arquivo pra mim?" -> O usuário quer um arquivo. É `CreateDocument`.
        - **Cenário de Exclusão (NÃO invocar):**
            - Prompt: "gosto de criar documentos" -> É uma afirmação, não um pedido. Usar `GeneralChat`.

        **2. Ferramenta `FillTemplate`:**
        - **DIRETIVA:** Invoque **SOMENTE SE** as duas condições a seguir forem atendidas: (1) O usuário menciona a palavra "template" (ou sinônimos como "modelo", "padrão"). (2) O usuário fornece um nome de arquivo que termina em `.docx`.
        - **Exemplo de Alta Confiança:**
            - Prompt: "usar o modelo 'proposta_cliente_v2.docx' para a empresa Soluções Alfa"
            - Invocação Correta: `FillTemplate(template_name='proposta_cliente_v2.docx', topic='proposta para a empresa Soluções Alfa')`
        - **Cenário de Exclusão (NÃO invocar):**
            - Prompt: "qual o melhor template para propostas?" -> É uma pergunta sobre templates. Usar `GeneralChat`.
            - Prompt: "use o modelo que te passei antes" -> Ambíguo, sem nome de arquivo explícito. Usar `GeneralChat` para pedir esclarecimento.

        **3. Ferramenta `ReadDocument`:**
        - **DIRETIVA:** Invoque **SOMENTE SE** `has_attachment=True` E o prompt do usuário for uma pergunta ou comando **sobre o conteúdo do anexo**.
        - **Exemplo de Alta Confiança:**
            - Prompt: "faça um resumo dos pontos principais deste arquivo"
            - Invocação Correta: `ReadDocument(question='resumo dos pontos principais do arquivo')`
        - **Exemplo de Alta Confiança 2:**
            - Prompt: "quais foram os valores de vendas de maio segundo a planilha?"
            - Invocação Correta: `ReadDocument(question='valores de vendas de maio segundo a planilha')`
        - **Cenário de Exclusão (NÃO invocar):**
            - Prompt: "arquivo recebido, obrigado!" (`has_attachment=True`) -> É uma confirmação, não um comando. Usar `GeneralChat`.

        **4. Ferramenta `GeneralChat` (A Regra de Ouro do Fallback):**
        - **DIRETIVA:** Se a solicitação não se encaixar **PERFEITAMENTE** em nenhuma das 3 categorias acima, **VOCÊ DEVE USAR `GeneralChat`**. É melhor pedir esclarecimento do que executar a ação errada.
        - **Exemplos de Alta Confiança:**
            - Prompt: "bom dia" -> `GeneralChat(user_request='bom dia')`
            - Prompt: "o que vc pode fazer?" -> `GeneralChat(user_request='o que vc pode fazer?')`
            - Prompt: "liste os templates" -> `GeneralChat(user_request='liste os templates')` (O nó de chat saberá como lidar com isso).
            - Prompt: "interessante esse relatório" -> `GeneralChat(user_request='interessante esse relatório')`
            - Prompt: "converta o documento anterior para pdf" -> Ambíguo. O sistema atual não tem um fluxo de "conversão". `GeneralChat` deve lidar com a resposta.

        **Processo de Raciocínio (Seu Monólogo Interno):**
        1.  O usuário pediu para **CRIAR** um arquivo do zero? -> `CreateDocument`.
        2.  O usuário mencionou "template" E um nome de arquivo `.docx`? -> `FillTemplate`.
        3.  Há um anexo E o usuário está perguntando sobre ele? -> `ReadDocument`.
        4.  Nenhuma das anteriores? -> `GeneralChat`. Sem exceções.

        **FORMATO DE SAÍDA:** Invoque a ferramenta escolhida com os argumentos corretamente preenchidos.
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