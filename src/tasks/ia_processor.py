# Arquivo: /src/tasks/ia_processor.py

import json
from datetime import datetime
from bson import ObjectId

from crewai import Crew, Process, Task
from src.db.mongo import get_db
# Importa todos os agentes que a Crew pode precisar
from src.tasks.agents import agente_roteador, agente_executor_de_arquivos, agente_conversador

# Esta função principal é a única que precisa ser importada pelas rotas.
def processar_solicitacao_ia(message_id: str) -> str:
    """
    Orquestra uma equipe de agentes de IA usando um processo hierárquico
    para processar uma solicitação de usuário de forma inteligente.
    """
    print(f"Orquestrando CrewAI com processo hierárquico para a mensagem: {message_id}")
    db = get_db()
    
    try:
        # 1. Obter Contexto Completo da Conversa
        mensagem_atual = db.messages.find_one({"_id": ObjectId(message_id)})
        if not mensagem_atual:
            print(f"ERRO: Mensagem com ID {message_id} não encontrada.")
            return "Falha"

        conversation_id = mensagem_atual.get("conversation_id")
        user_id = str(mensagem_atual.get("user_id"))
        input_doc_id = str(mensagem_atual.get("input_document_id")) if mensagem_atual.get("input_document_id") else "Nenhum"

        historico_cursor = db.messages.find(
            {"conversation_id": conversation_id}
        ).sort("timestamp", 1)
        historico_completo = list(historico_cursor)
        
        # Constrói uma representação em texto do histórico para o agente entender
        historico_texto = "\n".join(
            [f"{msg['role']}: {msg['content']}" for msg in historico_completo]
        )

        # 2. Montar a Tarefa Principal para o Gerente
        tarefa_principal = Task(
            description=(
                "Você é o gerente. Sua tarefa é analisar o pedido do usuário e o histórico para delegar o trabalho ao especialista correto. "
                "Siga as regras de decisão abaixo.\n\n"

                "**NOVA REGRA IMPORTANTE:** Se a ação envolve criar um documento, você DEVE inferir um nome de arquivo descritivo "
                "e relevante a partir do contexto (ex: 'proposta_cliente_x.docx') e incluí-lo como o parâmetro 'output_filename' "
                "na sua delegação para o especialista.\n\n"

                "**REGRAS DE DECISÃO (EM ORDEM DE PRIORIDADE):**\n"
                "1. **VERIFIQUE O HISTÓRICO:** Se a resposta já estiver no histórico, delegue ao 'Especialista em Conversação' com a resposta pronta.\n"
                "2. **USE FERRAMENTAS:** Se precisar de ferramentas, delegue ao 'Especialista em Documentos' com um plano de ação claro, especificando a ferramenta e TODOS os parâmetros necessários ('document_id', 'context', 'owner_id', 'output_filename', etc.).\n"
                "3. **PERGUNTA GERAL:** Se for uma pergunta geral, delegue ao 'Especialista em Conversação'.\n\n"

                f"**INFORMAÇÕES DISPONÍVEIS:**\n"
                f"- ID do usuário (owner_id): '{user_id}'\n"
                f"- ID do documento anexado: '{input_doc_id}'\n\n"

                "**Histórico da Conversa:**\n"
                f"--- INÍCIO DO HISTÓRICO ---\n{historico_texto}\n--- FIM DO HISTÓRICO ---"
            ),
            expected_output=(
                "O resultado final da tarefa que foi delegada. Pode ser uma resposta em texto ou o resultado da execução de uma ferramenta."
            ),
            agent=agente_roteador # Esta tarefa é entregue ao gerente para ele orquestrar.
        )

        # 3. Montar e Executar a Crew com Processo Hierárquico
        crew = Crew(
            agents=[agente_executor_de_arquivos, agente_conversador], # A lista de trabalhadores que o gerente pode usar.
            tasks=[tarefa_principal], # Apenas a tarefa principal a ser gerenciada.
            process=Process.hierarchical,
            manager_llm=agente_roteador.llm, # O cérebro do gerente.
            verbose=True
        )

        resultado_crew = crew.kickoff()

        print(f"Resultado final da Crew: {resultado_crew}")

        # 4. Processar o Resultado e Atualizar o Banco de Dados
        
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
                pass 

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