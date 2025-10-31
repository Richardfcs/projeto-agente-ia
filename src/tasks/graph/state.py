# src/tasks/graph/state.py

"""
Define o Contrato de Dados (Estado) para o Grafo de IA.

Este arquivo é o ponto central de verdade para toda a informação que flui
através do nosso sistema de IA. A classe `GraphState` é uma TypedDict, que nos dá
a flexibilidade de um dicionário com a segurança de tipos do Python.

Cada chave nesta classe representa uma peça de informação que pode ser lida
ou escrita por qualquer nó no grafo. Isso torna a passagem de contexto entre
as etapas explícita e robusta, eliminando a necessidade de "adivinhar" o que
a etapa anterior produziu.

Esta estrutura substitui completamente a necessidade do antigo `memory_manager.py`,
integrando a memória de curto prazo diretamente na execução do fluxo.
"""

from typing import List, Dict, TypedDict, Optional, Any

class GraphState(TypedDict):
    """
    Representa o estado completo do nosso grafo de IA para uma única execução.

    Atributos:
        # --- Atributos de Entrada (Fornecidos no início) ---
        user_id: ID do usuário que iniciou a requisição. Essencial para permissões.
        conversation_id: ID da conversa atual para buscar histórico e salvar mensagens.
        prompt: A mensagem exata enviada pelo usuário.
        input_document_id: O ID do documento que o usuário anexou (se houver).
        conversation_history: Lista de dicionários representando o histórico da conversa.

        # --- Atributos de Execução (Preenchidos pelos nós durante o fluxo) ---
        intent: A intenção do usuário, classificada pelo `router_node`.
                 Ex: "PREENCHER_TEMPLATE", "CONVERSA_GERAL".
                 Este campo é crucial para o roteamento condicional no grafo.

        tool_output: Armazena a saída da última ferramenta executada.
                     Padronizado para ser um dicionário (proveniente da sua `ToolResponse`),
                     permitindo que nós subsequentes acessem `status`, `data`, etc.

        generation: Armazena o conteúdo gerado por uma chamada de LLM que não seja
                    uma resposta final (ex: o JSON de contexto para um template).

        # --- Atributos de Saída (Usados para finalizar o processo) ---
        final_response: A resposta final em texto a ser enviada ao usuário.
                        Gerada pelo `final_response_node` ou por fluxos de chat.
        
        generated_document_id: O ID do novo documento criado por uma ferramenta,
                               para ser salvo na mensagem de resposta do assistente.
    """
    
    # --- Atributos de Entrada ---
    user_id: str
    conversation_id: str
    prompt: str
    input_document_id: Optional[str]
    conversation_history: List[Dict[str, Any]]

    # --- Atributos de Execução ---
    intent: Optional[str]
    routed_tool_call: Optional[Dict[str, Any]]
    tool_output: Optional[Dict[str, Any]]
    generation: Optional[Any] # Pode ser string, dict, etc.
    required_fields: Optional[List[str]]

    # --- Atributos de Saída ---
    final_response: Optional[str]
    generated_document_id: Optional[str]
    suggested_filename: Optional[str]