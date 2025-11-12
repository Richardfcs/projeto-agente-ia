# src/tasks/graph/nodes.py

"""
Implementação dos Nós do Grafo de IA (Os Músculos da Nossa Lógica).

Este arquivo contém as funções que executam o trabalho real em cada etapa
do nosso fluxo de IA, conforme definido em `builder.py`.

Cada função aqui é um "nó" e segue um contrato simples:
1.  Recebe o `GraphState` atual como seu único argumento.
2.  Executa uma tarefa específica (rotear, chamar um LLM, usar uma ferramenta, etc.).
3.  Retorna um dicionário contendo apenas as chaves do `GraphState` que deseja atualizar.

Esta abordagem modular torna o sistema fácil de entender, manter e estender.
"""

import json
import re
from typing import Dict, Any
from pydantic import BaseModel, Field

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.exceptions import OutputParserException
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableParallel

from src.config import Config
from .state import GraphState
from src.services.intelligent_router import IntelligentRouter, CreateDocument, FillTemplate, ReadDocument, GeneralChat
from src.tasks.tools import (
    template_lister_tool,
    template_inspector_tool,
    template_filler_tool,
    file_reader_tool,
)
from src.utils.observability import log_with_context

from src.utils.markdown_converter import convert_markdown_to_docx_stream, convert_markdown_to_pdf_stream
from src.tasks.file_generators import criar_xlsx_stream # Usaremos a de xlsx diretamente aqui
from src.tasks.tools import save_file_tool # Importe a nova ferramenta

logger = log_with_context(component="GraphNodes")

# --- Inicialização do LLM (Gemini) ---
# Instanciamos o LLM uma única vez para ser reutilizado por todos os nós.
# `convert_system_message_to_human=True` é uma boa prática para compatibilidade com Gemini.
try:
    llm = ChatGoogleGenerativeAI(
        model="gemini-2.5-flash-lite",
        google_api_key=Config.GOOGLE_API_KEY,
        convert_system_message_to_human=True,
        temperature=0.7,
    )
    logger.info("LLM Gemini inicializado com sucesso.")
except Exception as e:
    logger.error("Falha ao inicializar o LLM Gemini. Verifique a GOOGLE_API_KEY.", error=str(e))
    llm = None

# --- Helper Functions (Extraídas do antigo ia_processor) ---

class TemplateOutput(BaseModel):
    suggested_filename: str = Field(description="Um nome de arquivo lógico e descritivo em formato snake_case, terminando em .docx. Ex: relatorio_inspecao_global_corp.docx")
    context: dict = Field(description="O dicionário JSON com as chaves e valores para preencher o template.")

class DocumentOutput(BaseModel):
    suggested_filename: str = Field(description="Um nome de arquivo lógico e descritivo em formato snake_case, com a extensão correta (.docx, .xlsx, ou .pdf).")
    content: str = Field(description="O conteúdo textual completo para o corpo do documento.")

def _get_template_name_from_state(state: GraphState) -> str | None:
    """Extrai o nome do arquivo do template do prompt do usuário."""
    match = re.search(r"['\"]?([\w\d_\-]+\.docx?)['\"]?", state["prompt"], re.IGNORECASE)
    return match.group(1) if match else None

# --- Implementação dos Nós ---

def router_node(state: GraphState) -> Dict[str, Any]:
    """
    Primeiro nó do grafo: usa o roteador inteligente baseado em Tool Calling
    para classificar a intenção do usuário de forma semântica.
    """
    logger.info("Executando router_node (Intelligent)", conversation_id=state["conversation_id"])
    if not llm:
        return {"final_response": "Erro crítico: O modelo de linguagem (LLM) não está disponível."}
        
    router = IntelligentRouter()
    has_attachment = bool(state.get("input_document_id"))
    
    # Roteia e obtém uma classe Pydantic como resultado
    tool_name, tool_args = router.route(state["prompt"], state["conversation_history"], has_attachment)
    
    logger.info(f"Intenção roteada para: {tool_name} com args: {tool_args}")

    # Armazena a chamada completa no estado para os próximos nós usarem
    return {"routed_tool_call": {"tool": tool_name, "args": tool_args}}

def fill_template_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Executa o fluxo completo para preencher um template, agora com a capacidade
    de gerar conteúdo criativamente quando as informações não são fornecidas.
    """
    logger.info("Executando fill_template_flow_node (fase de extração/geração)", conversation_id=state["conversation_id"])
    tool_args = state["routed_tool_call"]["args"]
    template_name = tool_args.get("template_name")
    topic = tool_args.get("topic") # O tópico principal agora é crucial

    if not template_name:
        return {"final_response": "Não consegui identificar qual template você gostaria de usar."}

    # 1. Inspecionar o template
    inspector_result = template_inspector_tool.invoke({"template_name": template_name})
    if inspector_result.get("status") == "error":
        return {"tool_output": inspector_result}

    inspector_data = inspector_result.get("data", {})
    required_fields = inspector_data.get("all_required", [])
    collections_fields = inspector_data.get("collections", [])
    
    # Se não houver campos, podemos pular direto para o preenchimento com um JSON vazio.
    if not required_fields:
        logger.info("Template não tem placeholders. Gerando documento vazio.")
        suggested_filename = f"{template_name.split('.')[0]}_{topic.replace(' ', '_')[:20]}.docx"
        return {"generation": {}, "required_fields": [], "suggested_filename": suggested_filename}

    # --- PROMPT DE EXTRAÇÃO E GERAÇÃO (VERSÃO ESPECIALISTA v2) ---
    prompt_template = f"""
    **PERSONA:** Você é um Redator Técnico e Analista de Dados. Sua função é preencher uma estrutura JSON para um relatório técnico com base em uma solicitação do usuário. Você é capaz tanto de extrair informações quanto de gerar conteúdo plausível quando necessário.

    **CONTEXTO:**
    - Solicitação do Usuário: "{topic}"
    - Histórico da Conversa: {state['conversation_history']}
    - Template Alvo: '{template_name}'
    - Estrutura de Dados Requerida (Chaves do JSON): {required_fields}
    - Desses campos, os seguintes são LISTAS (para loops): {collections_fields}

    **TAREFA:** Preencha o JSON abaixo. Você deve seguir a "Lógica de Preenchimento Híbrida e Sugerir um nome de arquivo (`suggested_filename`) descritivo, em `snake_case`, terminando em `.docx`. O nome deve refletir o tópico principal do documento.".

    **LÓGICA DE PREENCHIMENTO HÍBRIDA (REGRAS CRÍTICAS):**

    1.  **EXTRAIR PRIMEIRO:** Sempre priorize as informações fornecidas pelo usuário no histórico ou na solicitação.
    2.  **GERAR DEPOIS (PARA CAMPOS DE TEXTO):** Se a informação para um campo de texto (`titulo_documento`, `subtitulo_documento`, `sumario_documento`, `secao.titulo`, `secao.conteudo`, `texto_conclusao`) NÃO for fornecida, **VOCÊ DEVE GERAR** um conteúdo apropriado e profissional com base no tópico principal ("{topic}").
    3.  **NÃO GERAR DADOS TABULARES:** Para campos de dados estruturados como `dados_coletados`, se a informação não for fornecida, o valor DEVE ser uma lista vazia `[]`. **NÃO INVENTE DADOS NUMÉRICOS OU DE MEDIÇÃO.**
    4.  **LISTAS VAZIAS:** Para campos de lista como `secoes`, se o usuário não especificar nenhuma seção, mas o tópico for complexo, sinta-se à vontade para gerar 2 ou 3 seções relevantes (ex: "Introdução", "Desenvolvimento", "Conclusão"). Se o tópico for muito simples, use uma lista vazia `[]`.
    5.  **DATAS:** Para campos de data como `data_documento`, use a data atual no formato 'DD de MMMM de AAAA' se não for especificada.

    **EXEMPLO DE USO COM O TEMPLATE 'TEMPLATE_TPF.docx':**

    *   **Cenário 1 (Prompt Vago):**
        *   Solicitação do Usuário: "Crie um relatório para a Global Corp sobre inspeção de drones em linhas de transmissão."
        *   Seu Raciocínio: "O usuário deu um bom tópico, mas nenhum detalhe. Vou gerar o conteúdo."
        *   Saída JSON Esperada (Exemplo):
          ```json
          {{
            "titulo_documento": "Relatório de Inspeção de Linhas de Transmissão com Drones",
            "subtitulo_documento": "Análise para Cliente: Global Corp",
            "data_documento": "31 de Outubro de 2025",
            "sumario_documento": "Este documento apresenta os resultados da inspeção aérea realizada com VANTs (Veículos Aéreos Não Tripulados) nas linhas de transmissão designadas, detalhando as anomalias encontradas e as recomendações técnicas.",
            "secoes": [
              {{
                "titulo": "1. Introdução",
                "conteudo": "A inspeção aérea com drones representa uma evolução na manutenção preditiva de ativos elétricos, permitindo a identificação de defeitos com maior segurança e eficiência...",
                "subsecoes": []
              }},
              {{
                "titulo": "2. Metodologia Aplicada",
                "conteudo": "Foram utilizados drones do modelo DJI Matrice 300 RTK equipados com sensores térmicos e RGB de alta resolução...",
                "subsecoes": []
              }}
            ],
            "dados_coletados": [],
            "texto_conclusao": "A inspeção revelou-se eficaz, e recomenda-se a atuação das equipes de manutenção nos pontos críticos identificados para garantir a integridade do sistema."
          }}
          ```

    *   **Cenário 2 (Prompt com Detalhes):**
        *   Solicitação: "Use o TEMPLATE_TPF.docx. Título: Relatório de Campo. Seção 1: 'Visita Técnica', conteúdo: 'A visita ocorreu na segunda-feira'. Dados: Local 'Torre 15', Med_A '35.2', Med_B '40.1'."
        *   Seu Raciocínio: "O usuário deu detalhes específicos. Vou usá-los e gerar o resto."
        *   Saída JSON Esperada (Exemplo):
          ```json
          {{
            "titulo_documento": "Relatório de Campo",
            "subtitulo_documento": "Análise Preliminar",
            "data_documento": "31 de Outubro de 2025",
            "sumario_documento": "Este documento detalha os achados da visita técnica de campo, incluindo medições iniciais.",
            "secoes": [
              {{
                "titulo": "Visita Técnica",
                "conteudo": "A visita ocorreu na segunda-feira.",
                "subsecoes": []
              }}
            ],
            "dados_coletados": [
              {{
                "local": "Torre 15",
                "med_A": "35.2",
                "med_B": "40.1"
              }}
            ],
            "texto_conclusao": "As medições iniciais indicam a necessidade de uma análise mais aprofundada."
          }}
          ```

    **EXEMPLO DE RACIOCÍNIO E SAÍDA:**
    - Solicitação do Usuário: "Crie um relatório para a Global Corp sobre inspeção de drones."
    - Seu Raciocínio: "O tópico é 'inspeção de drones para a Global Corp'. Um bom nome de arquivo seria 'relatorio_inspecao_drones_global_corp.docx'. Vou gerar o conteúdo para os campos de texto e deixar os dados tabulares vazios."
    - Saída JSON Correta (Exemplo):
      ```json
      {{
        "suggested_filename": "relatorio_inspecao_drones_global_corp.docx",
        "context": {{
          "titulo_documento": "Relatório de Inspeção com Drones",
          "subtitulo_documento": "Cliente: Global Corp",
          "data_documento": "31 de Outubro de 2025",
          "sumario_documento": "Este documento detalha os resultados da inspeção...",
          "secoes": [],
          "dados_coletados": [],
          "texto_conclusao": "A inspeção foi um sucesso."
        }}
      }}
      ```

    **FORMATO DE SAÍDA OBRIGATÓRIO:** Responda APENAS com o bloco JSON estruturado com as chaves 'suggested_filename' e 'context'.
    """
    
    parser = PydanticOutputParser(pydantic_object=TemplateOutput)
    chain = llm | parser
    
    try:
        # A saída do LLM agora será um objeto Pydantic TemplateOutput
        output_object = chain.invoke(prompt_template)
        generated_json = output_object.context
        suggested_filename = output_object.suggested_filename
    except Exception as e:
        logger.error("Falha ao gerar ou parsear o JSON estruturado do LLM.", error=str(e))
        return { "final_response": "Tive um problema ao gerar o conteúdo para o seu documento." }

    # Passa o JSON e o nome do arquivo para o próximo nó
    return {
        "generation": generated_json,
        "required_fields": required_fields,
        "suggested_filename": suggested_filename
    }

def read_document_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Executa o fluxo de leitura de um documento anexado, adaptando o prompt
    da IA com base no tipo de conteúdo do arquivo (texto, tabela, json, etc.).
    """
    logger.info("Executando read_document_flow_node", conversation_id=state["conversation_id"])
    document_id = state.get("input_document_id")
    # Se o roteador não extraiu uma pergunta específica, usamos o prompt inteiro do usuário.
    question = state["routed_tool_call"]["args"].get("question", state["prompt"])

    if not document_id:
        return {"final_response": "Por favor, anexe um documento para que eu possa lê-lo."}

    # 1. Ler o conteúdo do arquivo
    reader_result = file_reader_tool.invoke({"document_id": document_id})
    if reader_result.get("status") == "error":
        return {"tool_output": reader_result}

    reader_data = reader_result.get("data", {})
    file_content = reader_data.get("content", "")
    content_type = reader_data.get("content_type", "text")  # Ex: 'excel', 'pdf', 'csv'...

    # Verifica se o conteúdo extraído está vazio.
    if not file_content or not file_content.strip():
        return {"final_response": "O documento parece estar vazio ou não contém texto para ser lido."}

    # --- INÍCIO DA LÓGICA DE PROMPT DINÂMICO ---
    
    # 2. Define a Persona e Instruções Específicas com base no tipo de conteúdo
    if content_type in ["excel", "csv"]:
        persona_and_instructions = """
        **PERSONA:** Você é um Analista de Dados especialista em interpretar dados tabulares apresentados em formato de texto.
        
        **INSTRUÇÕES ADICIONAIS:** A "Fonte de Verdade" abaixo é uma tabela. Analise sua estrutura de colunas e linhas para responder à pergunta. Você tem permissão para fazer inferências, raciocinar e executar cálculos simples (somas, contagens, encontrar valores máximos/mínimos) com base nos dados da tabela para chegar à resposta correta.
        """
    elif content_type == "json":
        persona_and_instructions = """
        **PERSONA:** Você é um Engenheiro de Software especialista em estruturas de dados.
        
        **INSTRUÇÕES ADICIONAIS:** A "Fonte de Verdade" é um documento JSON. Navegue pela estrutura de chaves, valores, objetos e listas para encontrar a informação solicitada.
        """
    else:  # Para 'docx', 'pdf', 'txt' e outros tipos de texto puro
        persona_and_instructions = """
        **PERSONA:** Você é um Assistente de Pesquisa especialista em análise textual.
        
        **INSTRUÇÕES ADICIONAIS:** Leia e interprete o texto a seguir para encontrar a resposta para a pergunta do usuário.
        """

    # 3. Monta o prompt final combinando as partes
    base_prompt_template = """
    **CONTEXTO:**
    - Pergunta do Usuário: '{question}'
    - Conteúdo do Documento Anexado (Fonte de Verdade):
    ---
    {file_content}
    ---

    **TAREFA:** Com base **EXCLUSIVAMENTE** na "Fonte de Verdade" acima, responda à pergunta do usuário.

    **PROTOCOLO DE RESPOSTA (REGRAS CRÍTICAS):**
    1.  NÃO utilize conhecimento externo ou informações da internet.
    2.  Se a resposta não puder ser encontrada ou inferida a partir dos dados do documento, sua única resposta permitida é: "Com base na análise do documento, não encontrei uma resposta para a sua pergunta."
    3.  Seja direto e preciso em sua resposta. Forneça o resultado final sem explicações excessivas sobre como você chegou a ele, a menos que a pergunta peça isso.
    """

    full_prompt = persona_and_instructions + base_prompt_template
    
    # Formata o prompt com os dados reais, limitando o tamanho do conteúdo para evitar exceder limites de token
    final_prompt_text = full_prompt.format(question=question, file_content=file_content[:15000])
    
    # 4. Usar o LLM para responder com base no prompt contextualizado
    response = llm.invoke(final_prompt_text)
    return {"final_response": response.content}

def create_document_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Executa o fluxo de criação de documentos com uma abordagem robusta de "Separação de Responsabilidades":
    1. Gera o conteúdo em Markdown (ou texto tabular) em uma chamada de LLM.
    2. Gera o nome do arquivo em outra chamada de LLM (executado em paralelo).
    3. Combina os resultados em Python para a conversão e salvamento.
    """
    logger.info("Executando create_document_flow_node (com Separação de Responsabilidades)", conversation_id=state["conversation_id"])
    tool_args = state["routed_tool_call"]["args"]
    topic = tool_args.get("topic")
    file_type = tool_args.get("file_type")

    # --- INÍCIO DA NOVA ARQUITETURA DE GERAÇÃO ---

    try:
        # TAREFA 1: CADEIA PARA GERAR APENAS O CONTEÚDO
        content_prompt = ChatPromptTemplate.from_template(
            """
            **PERSONA:** Você é um Redator Especialista que estrutura todo o seu conteúdo usando Markdown para garantir uma formatação rica.
            
            **TAREFA:** Escreva um conteúdo textual completo e detalhado sobre o tópico: "{topic}".
            
            **REGRAS DE FORMATAÇÃO DO CONTEÚDO:**
            - Se o formato de destino final for `docx` ou `pdf`, VOCÊ DEVE USAR SINTAXE MARKDOWN (títulos com '#', negrito com '**', listas com '-',  `---` para criar linhas de separação, `| Cabeçalho |` ... para criar tabelas simples, ``` para blocos de código e `código inline` etc.).
            - Se o formato de destino final for `xlsx`, sua saída deve ser texto tabular (cabeçalho na primeira linha, colunas separadas por ';', e `\\n` para novas linhas).
            
            **IMPORTANTE:** Sua resposta deve conter APENAS o conteúdo bruto, sem nenhum comentário ou texto introdutório.
            """
        )
        content_chain = content_prompt | llm | StrOutputParser()

        # TAREFA 2: CADEIA PARA GERAR APENAS O NOME DO ARQUIVO
        filename_prompt = ChatPromptTemplate.from_template(
            """
            **PERSONA:** Você é um assistente de arquivamento de IA.
            
            **TAREFA:** Com base no tópico a seguir, sugira um nome de arquivo curto, descritivo e em `snake_case`.
            
            **REGRAS:**
            1. O nome do arquivo DEVE ter a extensão `.{file_type}`.
            2. A resposta deve conter APENAS o nome do arquivo e nada mais.
            
            **TÓPICO:** "{topic}"
            """
        )
        filename_chain = filename_prompt | llm | StrOutputParser()

        # EXECUTAR AMBAS AS TAREFAS EM PARALELO PARA EFICIÊNCIA
        # O resultado será um dicionário como {'content': '...', 'suggested_filename': '...'}
        chain = RunnableParallel(
            content=content_chain,
            suggested_filename=filename_chain,
        )
        
        # Invoca a cadeia paralela
        generation_result = chain.invoke({"topic": topic, "file_type": file_type})
        
        generated_content = generation_result["content"]
        suggested_filename = generation_result["suggested_filename"].strip().replace(" ", "_") # Limpeza extra

        # Validação para garantir que o nome do arquivo está limpo e com a extensão correta
        if not suggested_filename.endswith(f'.{file_type}'):
             base_name = ".".join(suggested_filename.split('.')[:-1]) or suggested_filename
             suggested_filename = f"{base_name}.{file_type}"


    except Exception as e:
        logger.error(f"Falha ao gerar conteúdo ou nome de arquivo do LLM.", error=str(e))
        return { "final_response": "Tive um problema ao gerar o conteúdo para o seu documento." }

    # --- FIM DA NOVA ARQUITETURA DE GERAÇÃO ---

    # --- LÓGICA DE CONVERSÃO E SALVAMENTO (Mantida da sua implementação) ---
    
    file_stream = None
    try:
        if file_type == 'docx':
            file_stream = convert_markdown_to_docx_stream(generated_content)
        elif file_type == 'pdf':
            file_stream = convert_markdown_to_pdf_stream(generated_content)
        elif file_type == 'xlsx':
            topicos = [line for line in generated_content.split('\n') if line]
            file_stream = criar_xlsx_stream(topicos, filename=suggested_filename)
        
        if file_stream:
            save_result = save_file_tool.invoke({
                "filename": suggested_filename,
                "content_stream": file_stream.getvalue(),
                "owner_id": state["user_id"]
            })
            return {"tool_output": save_result}
        else:
            raise ValueError(f"Tipo de arquivo não suportado para geração: {file_type}")

    except Exception as e:
        logger.exception("Erro durante a conversão ou salvamento do arquivo.", error=str(e))
        return {"final_response": f"Desculpe, ocorreu um erro ao formatar e salvar o seu documento {file_type}."}

def general_chat_flow_node(state: GraphState) -> Dict[str, Any]:
    """
    Nó de fallback para conversas gerais, Q&A, e outras intenções simples.
    """
    logger.info("Executando general_chat_flow_node", conversation_id=state["conversation_id"])
    
    # --- CORREÇÃO APLICADA AQUI ---
    # Inspeciona o pedido original do usuário que foi passado pelo roteador.
    user_request = state["routed_tool_call"]["args"].get("user_request", "").lower()
    
    # Verifica por palavras-chave relacionadas a templates.
    if any(word in user_request for word in ["template", "templates", "modelo", "modelos"]):
        logger.info("Detectada intenção de listar templates dentro do chat geral.")
        lister_result = template_lister_tool.invoke({})
        templates = lister_result.get("data", {}).get("templates", [])
        if templates:
            response_text = "Claro! Os templates disponíveis para uso são:\n- " + "\n- ".join(templates)
        else:
            response_text = "No momento, não há templates disponíveis no sistema."
        return {"final_response": response_text}

    # Para outras conversas, apenas chamamos o LLM com o histórico
    prompt = f"""
    **PERSONA:** Você é o TPF-AI, um assistente de IA amigável, prestativo e profissional.

    **CAPACIDADES:** Você pode conversar sobre diversos tópicos, ajudar com tarefas criativas (escrever poemas, resumos), responder a perguntas gerais e, o mais importante, você pode criar e manipular documentos.

    **CONTEXTO DA CONVERSA:**
    {state['conversation_history']}

    **TAREFA:** Responda à última mensagem do usuário (`{state['prompt']}`) de forma útil e engajadora, mantendo o contexto da conversa.

    **DIRETRIZES DE COMUNICAÇÃO:**
    - Seja sempre educado e claro.
    - Se você não souber uma resposta, diga que não sabe em vez de inventar.
    - Se a pergunta do usuário for ambígua, faça uma pergunta de esclarecimento para entender melhor a necessidade dele.
    - Mantenha as respostas relativamente concisas.

    **FORMATO DE SAÍDA:** Uma resposta em texto amigável.
    """
    response = llm.invoke(prompt)
    return {"final_response": response.content}

def final_response_node(state: GraphState) -> Dict[str, Any]:
    """
    Nó final que formata a saída de uma ferramenta em uma resposta amigável para o usuário.
    Se a resposta final já foi definida, ele apenas a repassa.
    """
    logger.info("Executando final_response_node", conversation_id=state["conversation_id"])
    
    if state.get("final_response"):
        # A resposta já foi gerada por um nó anterior (ex: chat geral), não faz nada.
        return {}

    tool_output = state.get("tool_output")
    if not tool_output:
        return {"final_response": "Desculpe, não consegui processar seu pedido."}

    if tool_output.get("status") == "success":
        final_message = tool_output.get("message", "Sua solicitação foi processada com sucesso!")
        doc_id = tool_output.get("data", {}).get("document_id")
        return {
            "final_response": final_message,
            "generated_document_id": doc_id
        }
    else: # status == "error"
        error_message = tool_output.get("message", "Ocorreu um erro desconhecido.")
        # Lógica para tornar o erro mais útil
        if tool_output.get("error_code") == "TEMPLATE_NOT_FOUND":
            lister_result = template_lister_tool.invoke({})
            templates = lister_result.get("data", {}).get("templates", [])
            if templates:
                error_message += "\n\nQue tal tentar um destes?:\n- " + "\n- ".join(templates)
        
        return {"final_response": error_message}

def validate_and_clarify_node(state: GraphState) -> Dict[str, Any]:
    """
    Verifica o JSON gerado. Se estiver muito vazio, formula uma pergunta
    para o usuário. Caso contrário, invoca a ferramenta de preenchimento.
    """
    logger.info("Executando validate_and_clarify_node", conversation_id=state["conversation_id"])
    
    if state.get("tool_output") and state["tool_output"].get("status") == "error":
        return {}
    
    generated_json = state.get("generation")
    if not isinstance(generated_json, dict):
        logger.error("O JSON gerado não é um dicionário válido. Não é possível preencher o template.", received=generated_json)
        return {"final_response": "Desculpe, ocorreu um erro interno ao preparar os dados do seu documento."}

    required_fields = state.get("required_fields", [])
    
    null_or_empty_count = 0
    if required_fields: # Evita divisão por zero
        for key in required_fields:
            value = generated_json.get(key)
            if value is None or (isinstance(value, list) and not value):
                null_or_empty_count += 1
            
    if len(required_fields) > 2 and null_or_empty_count > len(required_fields) / 2:
        logger.warning("JSON gerado está muito vazio. Pedindo esclarecimento ao usuário.")
        
        missing_fields = [key for key in required_fields if generated_json.get(key) is None]
        
        # Usa o LLM para formular uma pergunta amigável
        prompt = f"""
        **PERSONA:** Você é um assistente de IA proativo.
        
        **TAREFA:** O usuário pediu para preencher um template, mas forneceu pouca informação.
        Sua tarefa é formular uma pergunta clara para o usuário, pedindo as informações que faltam.
        
        **Informações que Faltam:** {missing_fields}
        
        **EXEMPLO:** Se faltam 'sumario_documento' e 'secoes', você pode dizer:
        "Entendido! Para criar o documento para a Global Corp, preciso de mais alguns detalhes. Você poderia me fornecer um breve sumário e os tópicos principais para as seções do documento?"
        
        **FORMATO DE SAÍDA:** Apenas o texto da pergunta para o usuário.
        """
        clarification_question = llm.invoke(prompt).content
        return {"final_response": clarification_question}
    
    else:
        logger.info("JSON é suficiente. Prosseguindo para o preenchimento do documento.")
        # Se o JSON for bom, chama a ferramenta de preenchimento
        tool_args = state["routed_tool_call"]["args"]
        template_name = tool_args.get("template_name")
        suggested_filename = state.get("suggested_filename")
        
        filler_result = template_filler_tool.invoke({
            "template_name": template_name,
            "context": generated_json,
            "owner_id": state["user_id"],
            "output_filename": suggested_filename
        })
        return {"tool_output": filler_result}