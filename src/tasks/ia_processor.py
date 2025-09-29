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

        tarefa_principal = Task(
            description=(
                "Você é o gerente de uma equipe de especialistas em IA. Sua missão é analisar a solicitação do usuário e o histórico da conversa para criar um plano de execução passo a passo. "
                "Seu único trabalho é decompor a solicitação principal em uma lista de subtarefas para sua equipe. "
                "Você deve identificar qual especialista é o mais adequado para cada subtarefa.\n\n"

                "**COMO CRIAR O PLANO DE TAREFAS:**\n"
                "Para cada etapa do plano, você deve definir claramente:\n"
                "- `description`: O que o especialista precisa fazer, com todo o contexto necessário.\n"
                "- `expected_output`: Qual é o resultado esperado para essa etapa específica.\n"

                "**LÓGICA DE FINALIZAÇÃO DA TAREFA:**\n"
                "1. **SE o objetivo é CRIAR UM DOCUMENTO** (usando um template ou gerando um novo arquivo):\n"
                "   - O plano deve ter pelo menos duas etapas: uma para o `Analista de Conteúdo` gerar o JSON, e a etapa final para o `Especialista em Documentos` preencher o template.\n"
                "   - A `expected_output` da última tarefa do `Especialista em Documentos` deve ser a mensagem de sucesso com o ID do documento.\n"
                "2. **SE o objetivo é uma PERGUNTA ou CONVERSA:**\n"
                "   - O plano deve ter uma única tarefa para o `Especialista em Conversação`, e o resultado será a resposta dele.\n"
                "3. **SE ocorrer um ERRO:**\n"
                "   - O plano deve delegar a mensagem de erro para o `Revisor Final` para que ele formule uma resposta amigável.\n\n"

                "**Sua Equipe de Especialistas (para atribuir as tarefas):**\n"
                "- `Especialista em Documentos`\n"
                "- `Analista de Conteúdo e Estrutura`\n"
                "- `Especialista em Conversação`\n"
                "- `Revisor Final e Especialista em Comunicação`\n\n"

                f"**INFORMAÇÕES CRÍTICAS PARA O PLANO:**\n"
                f"- ID do usuário (owner_id): '{user_id}'\n"
                f"- ID do documento anexado pelo usuário: '{input_doc_id}'\n\n"

                f"--- HISTÓRICO DA CONVERSA ---\n{historico_texto}"
            ),
            expected_output=(
                "Uma lista de tarefas detalhadas e prontas para serem executadas pela equipe, seguindo a lógica de finalização."
            ),
            agent=agente_gerente
        )

        crew = Crew(
            agents=[agente_especialista_documentos, agente_analista_de_conteudo, agente_conversador, agente_revisor_final],
            tasks=[tarefa_principal],
            process=Process.hierarchical,
            manager_llm=agente_gerente.llm,
            verbose=True,
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