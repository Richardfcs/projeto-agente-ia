# Arquivo: /src/tasks/ia_processor.py

import json
from datetime import datetime
from bson import ObjectId

from crewai import Crew, Process, Task
from src.db.mongo import get_db
# Importa todos os agentes que a Crew pode precisar
from src.tasks.agents import agente_gerente, agente_especialista_documentos, agente_conversador, agente_analista_de_conteudo, agente_revisor_final

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

        # Tarefa 1: O Executor inspeciona o template para descobrir o que ele precisa.
        tarefa_inspecao = Task(
            description=(
                "Sua primeira tarefa é descobrir quais placeholders (variáveis) um template de documento espera. "
                "Analise o histórico da conversa para encontrar o nome do template que o usuário mencionou. "
                "Use a ferramenta 'Inspetor de Placeholders de Template' para extrair a lista de variáveis necessárias.\n\n"
                f"Histórico para análise:\n{historico_texto}"
            ),
            expected_output="Uma lista de texto simples contendo os nomes das variáveis que o template precisa.",
            agent=agente_especialista_documentos # O especialista que tem a ferramenta
        )

       # Tarefa 2: O Analista de Conteúdo gera o JSON com base na lista de placeholders.
        tarefa_geracao_conteudo = Task(
            description=(
                "Sua tarefa é criativa. Você receberá uma lista de placeholders (o resultado da tarefa anterior) "
                "e o histórico completo da conversa. Com base no pedido original do usuário, gere o conteúdo "
                "para CADA placeholder da lista e monte um único dicionário JSON `context` com os resultados.\n\n"
                f"Histórico para análise:\n{historico_texto}"
            ),
            expected_output="Um dicionário JSON completo (`context`) contendo todas as chaves e valores para preencher o template.",
            agent=agente_analista_de_conteudo,
            context=[tarefa_inspecao] # Depende da conclusão da inspeção
        )

        # Tarefa 3: O Executor usa o JSON gerado para preencher o template.
        tarefa_preenchimento = Task(
            description=(
                "Sua tarefa final é de execução. Você receberá um dicionário JSON `context` (o resultado da tarefa anterior). "
                "Use a ferramenta 'Preenchedor de Templates de Documentos' para gerar o documento final. "
                "Você precisará extrair o nome do template e inferir um nome de arquivo a partir do histórico da conversa.\n\n"
                f"ID do usuário (owner_id) a ser usado: '{user_id}'\n"
                f"Histórico para análise:\n{historico_texto}"
            ),
            expected_output="A mensagem de sucesso da ferramenta 'Preenchedor de Templates de Documentos', incluindo o ID do novo documento.",
            agent=agente_especialista_documentos,
            context=[tarefa_geracao_conteudo] # Depende da conclusão da geração de conteúdo
        )

        tarefa_de_revisao = Task(
            description=(
                "Analise o resultado da tarefa anterior. Se for uma mensagem de sucesso, formate-a de forma amigável. "
                "Se for uma mensagem de erro (ex: 'template não encontrado'), explique o problema para o usuário em "
                "linguagem simples e use a ferramenta 'Listador de Templates Disponíveis' para sugerir alternativas."
            ),
            expected_output="A resposta final, formatada e amigável, para o usuário.",
            agent=agente_revisor_final,
            context=[tarefa_inspecao, tarefa_geracao_conteudo, tarefa_preenchimento]
        )

        crew = Crew(
            agents=[agente_especialista_documentos, agente_analista_de_conteudo],
            tasks=[tarefa_inspecao, tarefa_geracao_conteudo, tarefa_preenchimento, tarefa_de_revisao],
            process=Process.sequential,
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