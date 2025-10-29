# src/tasks/graph/builder.py

"""
Construtor do Grafo de Execução da IA (A Planta Baixa da Nossa Lógica).

Este arquivo é o coração da nova arquitetura com LangGraph. Sua única responsabilidade
é definir a estrutura do nosso fluxo de trabalho de IA:
1.  Quais são as etapas (Nós)?
2.  Como as etapas se conectam (Arestas)?
3.  Qual é o caminho a ser seguido com base em diferentes condições (Arestas Condicionais)?

Ele substitui completamente a complexa lógica de `if/elif` do antigo `ia_processor.py`,
transformando-a em um fluxo de estados explícito, previsível e fácil de depurar.

A instância `app_graph` é compilada uma única vez quando a aplicação inicia,
tornando a execução de cada requisição muito eficiente.
"""

from langgraph.graph import StateGraph, END
from .state import GraphState
from .nodes import (
    router_node,
    fill_template_flow_node,
    read_document_flow_node,
    create_document_flow_node,
    general_chat_flow_node,
    final_response_node
)

def build_graph():
    """
    Constrói e compila o grafo de execução da IA usando StateGraph.

    Returns:
        Um grafo compilado e executável.
    """
    workflow = StateGraph(GraphState)

    # --- ETAPA 1: Adicionar os Nós ---
    # Cada nó representa uma função ou um passo lógico no nosso processo.
    # Pense neles como as "estações de trabalho" em uma linha de montagem.
    workflow.add_node("router", router_node)
    workflow.add_node("fill_template_flow", fill_template_flow_node)
    workflow.add_node("read_document_flow", read_document_flow_node)
    workflow.add_node("create_document_flow", create_document_flow_node)
    workflow.add_node("general_chat_flow", general_chat_flow_node)
    workflow.add_node("final_responder", final_response_node)

    # --- ETAPA 2: Definir o Ponto de Entrada ---
    # O grafo sempre começará sua execução pelo nó 'router'.
    workflow.set_entry_point("router")

    # --- ETAPA 3: Adicionar Arestas Condicionais (O Cérebro do Roteamento) ---
    # Esta é a parte mais poderosa. Após a execução do nó 'router', o grafo
    # inspecionará o campo 'intent' no estado e decidirá para qual nó seguir.
    # Isso substitui a necessidade de um agente "Gerente" e a lógica `if/elif`.
    workflow.add_conditional_edges(
        "router",  # Nó de origem
        lambda state: state["routed_tool_call"]["tool"],  # Função que lê o nome da ferramenta
        {
            # Mapeamento: "NomeDaFerramenta" -> "nome_do_proximo_no"
            "FillTemplate": "fill_template_flow",
            "CreateDocument": "create_document_flow",
            "ReadDocument": "read_document_flow",
            "GeneralChat": "general_chat_flow",
        }
    )

    # --- ETAPA 4: Adicionar Arestas Normais (O Fluxo Sequencial) ---
    # Após a conclusão de cada fluxo de trabalho principal, todos devem convergir
    # para o nó 'final_responder', que prepara a resposta final para o usuário.
    # Depois do 'final_responder', o fluxo termina.
    workflow.add_edge('fill_template_flow', 'final_responder')
    workflow.add_edge('read_document_flow', 'final_responder')
    workflow.add_edge('create_document_flow', 'final_responder')
    workflow.add_edge('general_chat_flow', 'final_responder')
    
    # O nó 'final_responder' é o último passo antes de terminar a execução.
    workflow.add_edge('final_responder', END)

    # --- ETAPA 5: Compilar o Grafo ---
    # Transforma nossa definição de nós e arestas em um objeto executável.
    app = workflow.compile()
    
    # Adicionar um método para gerar uma imagem visual do grafo (ótimo para documentação e depuração)
    try:
        # Tenta gerar a imagem se as dependências estiverem instaladas
        # (pip install pygraphviz)
        app.get_graph().draw_png("ia_workflow_graph.png")
        print("Diagrama do grafo de IA salvo em 'ia_workflow_graph.png'")
    except ImportError:
        print("PyGraphviz não instalado. Pule a geração da imagem do grafo.")
        print("Para visualizar o grafo, instale: pip install pygraphviz")
    
    return app

# --- Instância Única e Compilada do Grafo ---
# O grafo é construído e compilado apenas uma vez quando este módulo é importado.
# Isso garante máxima performance, pois não reconstruímos o grafo a cada requisição.
app_graph = build_graph()