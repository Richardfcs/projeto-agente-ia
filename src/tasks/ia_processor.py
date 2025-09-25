# Arquivo: /src/tasks/ia_processor.py

import json
from datetime import datetime
from bson import ObjectId

from crewai import Crew, Process, Task

from src.db.mongo import get_db, get_gridfs
from src.tasks.agents import agente_roteador, agente_executor_de_arquivos, agente_conversador

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
        user_id = str(mensagem_atual.get("user_id")) # Pega o ID do usuário
        
        historico_cursor = db.messages.find(
            {"conversation_id": conversation_id}
        ).sort("timestamp", 1)
        historico_completo = list(historico_cursor)
        
        # Constrói uma representação em texto do histórico para o agente entender
        historico_texto = "\n".join(
            [f"{msg['role']}: {msg['content']}" for msg in historico_completo]
        )

        # pegar contexto do documento
        
        input_doc_id = None
        if mensagem_atual.get("input_document_id"):
            input_doc_id = str(mensagem_atual.get("input_document_id"))

        # 2. Montar as Tarefas Encadeadas
        
        # Tarefa 1: O Roteador cria o plano.
        tarefa_de_planejamento = Task(
            description=(
                "Crie um plano de ação detalhado para um 'Especialista em Documentos'. "
                "O plano deve instruir o especialista sobre qual ferramenta usar e com quais parâmetros exatos.\n\n"
                f"**INFORMAÇÕES DISPONÍVEIS:**\n"
                f"- ID do usuário (owner_id): '{user_id}'\n"
                f"- ID do documento anexado: '{input_doc_id}'\n\n"
                "**Histórico da Conversa:**\n"
                f"--- INÍCIO DO HISTÓRICO ---\n{historico_texto}\n--- FIM DO HISTÓRICO ---"
            ),
            expected_output="Um texto claro e detalhado contendo o plano de ação para o especialista.",
            agent=agente_roteador
        )

        # Tarefa 2: O Executor recebe o plano do Roteador e o executa.
        tarefa_de_execucao = Task(
            description=(
                "Siga o plano de ação fornecido para completar a solicitação do usuário. "
                "Execute as ferramentas exatamente como descrito no plano."
            ),
            expected_output="O resultado final da execução da(s) ferramenta(s).",
            agent=agente_executor_de_arquivos,
            # ESTA É A LINHA MÁGICA:
            # O resultado da `tarefa_de_planejamento` será injetado no contexto desta tarefa.
            context=[tarefa_de_planejamento]
        )

        # 3. Montar a Crew com Processo Sequencial
        crew = Crew(
            agents=[agente_roteador, agente_executor_de_arquivos],
            tasks=[tarefa_de_planejamento, tarefa_de_execucao],
            process=Process.sequential,
            verbose=True
        )

        resultado_crew = crew.kickoff()

        print(f"Resultado final da Crew: {resultado_crew}")

        # 3. Processar o Resultado e Atualizar o Banco de Dados
        
        # A lógica aqui pode ser refinada. Assumimos que o resultado da crew
        # é a resposta final do assistente.
        resposta_assistente = str(resultado_crew)
        generated_doc_id = None
        
        # Tenta extrair o ID do documento da resposta da ferramenta
        if "O ID do metadado do novo documento é:" in resposta_assistente:
            try:
                doc_id_str = resposta_assistente.split(":")[-1].strip()
                generated_doc_id = ObjectId(doc_id_str)
            except:
                pass # Se falhar, o generated_doc_id continua None

        assistant_message = {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": resposta_assistente,
            "generated_document_id": generated_doc_id,
            "user_id": ObjectId(user_id),
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
            "content": f"Ocorreu um erro interno ao processar sua solicitação.",
            "user_id": mensagem_atual.get("user_id") if 'mensagem_atual' in locals() else None,
            "timestamp": datetime.utcnow(),
            "is_error": True
        })
        return "Falha"