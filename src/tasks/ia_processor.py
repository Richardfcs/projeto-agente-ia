# Arquivo: /src/tasks/ia_processor.py

import json
from datetime import datetime
from bson import ObjectId

from crewai import Crew, Process, Task

from src.db.mongo import get_db, get_gridfs
from src.tasks.agents import agente_roteador, agente_executor_de_arquivos

# Esta função principal é a única que precisa ser importada pelas rotas.
def processar_solicitacao_ia(message_id: str) -> str:
    """
    Orquestra uma equipe de agentes de IA (CrewAI) para processar uma solicitação
    de usuário, com memória de conversa e capacidade de usar ferramentas.
    """
    print(f"Orquestrando CrewAI para a mensagem: {message_id}")
    db = get_db()
    
    try:
        # 1. Obter Contexto da Conversa
        mensagem_atual = db.messages.find_one({"_id": ObjectId(message_id)})
        if not mensagem_atual:
            print(f"ERRO: Mensagem com ID {message_id} não encontrada.")
            return "Falha"

        conversation_id = mensagem_atual.get("conversation_id")
        historico_cursor = db.messages.find(
            {"conversation_id": conversation_id}
        ).sort("timestamp", 1)
        historico_completo = list(historico_cursor)
        
        # Constrói uma representação em texto do histórico para o agente entender
        historico_texto = "\n".join(
            [f"{msg['role']}: {msg['content']}" for msg in historico_completo]
        )

        # 2. Montar a Equipe de Agentes (A Crew)
        
        # A primeira tarefa é para o agente Roteador. Ele deve analisar o pedido
        # e criar um plano detalhado para o próximo agente.
        tarefa_analise_e_planejamento = Task(
            description=(
                "Sua tarefa é analisar o histórico de conversa abaixo e, especificamente, "
                "o último pedido do 'user'. Com base nisso, você deve decidir qual ação o 'Especialista em Documentos' "
                "deve tomar e formular uma instrução clara para ele.\n\n"
                "**Ferramentas disponíveis para o Especialista:**\n"
                "- 'Leitor de Arquivos do Usuário': Use para ler o conteúdo de um arquivo que o usuário anexou.\n"
                "- 'Preenchedor de Templates': Use para preencher um template .docx com dados extraídos.\n\n"
                "**Histórico da Conversa:**\n"
                f"--- INÍCIO DO HISTÓRICO ---\n{historico_texto}\n--- FIM DO HISTÓRICO ---\n\n"
                "**Sua Resposta Final (Expected Output):**\n"
                "Deve ser uma instrução direta e detalhada para o 'Especialista em Documentos'.\n"
                "Exemplo 1: 'Use a ferramenta Leitor de Arquivos do Usuário com o ID do documento X para extrair o texto.'\n"
                "Exemplo 2: 'Use a ferramenta Preenchedor de Templates com o template chamado \"proposta.docx\" e o seguinte contexto JSON: {{\"cliente\": \"ABC Corp\", \"valor\": 5000}}.'"
            ),
            expected_output="Uma instrução clara e acionável para o próximo agente.",
            agent=agente_roteador
        )

        # A segunda tarefa é para o Executor. Ele pega o plano do Roteador e o executa.
        tarefa_execucao = Task(
            description=(
                "Execute o plano de ação formulado pelo 'Analista e Roteador de Tarefas'. "
                "Utilize as ferramentas à sua disposição para cumprir a instrução. "
                "Seu resultado final será a saída direta da ferramenta que você usar."
            ),

            expected_output="O texto lido de um arquivo, ou uma mensagem de sucesso indicando o ID do documento gerado.",
            agent=agente_executor_de_arquivos,
            context=[tarefa_analise_e_planejamento] # Depende da conclusão da primeira tarefa
        )

        # Configura e executa a Crew
        crew = Crew(
            agents=[agente_roteador, agente_executor_de_arquivos],
            tasks=[tarefa_analise_e_planejamento, tarefa_execucao],
            process=Process.sequential,
            verbose=True # Use 2 para ver o "pensamento" dos agentes em detalhe
        )

        resultado_crew = crew.kickoff()

        print(f"Resultado final da Crew: {resultado_crew}")

        # 3. Processar o Resultado e Atualizar o Banco de Dados
        
        # A lógica aqui pode ser refinada. Assumimos que o resultado da crew
        # é a resposta final do assistente.
        resposta_assistente = str(resultado_crew)
        generated_doc_id = None # Lógica para extrair ID do resultado, se houver.

        # Salva a resposta final do assistente no chat
        assistant_message = {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": resposta_assistente,
            "generated_document_id": generated_doc_id,
            "user_id": mensagem_atual.get("user_id"),
            "timestamp": datetime.utcnow()
        }
        db.messages.insert_one(assistant_message)
        
        db.conversations.update_one(
            {"_id": conversation_id},
            {"$set": {"last_updated_at": datetime.utcnow()}}
        )

        print(f"Processamento com CrewAI para a mensagem {message_id} concluído.")
        return "Sucesso"

    except Exception as e:
        print(f"ERRO CRÍTICO ao orquestrar a CrewAI para a mensagem {message_id}: {e}")
        # Lógica de tratamento de erro (salvar mensagem de falha no DB)
        db.messages.insert_one({
            "conversation_id": mensagem_atual.get("conversation_id") if 'mensagem_atual' in locals() else None,
            "role": "assistant",
            "content": f"Ocorreu um erro interno ao processar sua solicitação com a equipe de IA.",
            "user_id": mensagem_atual.get("user_id") if 'mensagem_atual' in locals() else None,
            "timestamp": datetime.utcnow(),
            "is_error": True
        })
        return "Falha"