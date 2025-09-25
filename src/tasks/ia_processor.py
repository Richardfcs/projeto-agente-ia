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

        # 2. Montar a Equipe de Agentes (A Crew)
        
        tarefa_de_analise_e_planejamento = Task(
            description=(
                "Sua tarefa é analisar o histórico de conversa e o último pedido do 'user'. "
                "Decida a intenção principal: é uma pergunta, uma tarefa de template, ou uma tarefa de criação de documento simples?\n\n"
                
                "**REGRAS DE DECISÃO:**\n"
                "1. Se for uma pergunta geral, delegue ao 'Especialista em Conversação'.\n"
                "2. Se o pedido envolve usar um template existente, delegue ao 'Especialista em Documentos' para usar a ferramenta 'Preenchedor de Templates'.\n"
                "3. Se o pedido envolve pegar um texto (um resumo, uma lista, etc.) e salvá-lo em um novo arquivo, "
                "delegue ao 'Especialista em Documentos' para usar a ferramenta 'Gerador de Documentos Simples'. Você deve extrair ou inferir o nome do arquivo de saída e o conteúdo.\n\n"
                
                f"**INFORMAÇÃO CRÍTICA:** O ID do usuário (owner_id) é '{user_id}'.\n\n"
                
                "**Histórico da Conversa:**\n"
                f"--- INÍCIO DO HISTÓRICO ---\n{historico_texto}\n--- FIM DO HISTÓRICO ---\n\n"
                
                "**Sua Resposta Final (Expected Output):**\n"
                "Uma descrição de tarefa para o especialista apropriado, incluindo a ferramenta e TODOS os parâmetros necessários."
            ),
            expected_output="Uma descrição de tarefa clara e acionável para o próximo agente especialista.",
            agent=agente_roteador
        )

        tarefa_de_conversacao = Task(
            description=(
                "Responda à pergunta do usuário contida no plano do 'Analista e Roteador de Tarefas'."
            ),
            expected_output="Uma resposta em texto, clara e concisa.",
            agent=agente_conversador,
            context=[tarefa_de_analise_e_planejamento]
        )

        tarefa_de_execucao = Task(
            description=(
                "Execute o plano de ação formulado pelo 'Analista e Roteador de Tarefas'. "
                "Utilize as ferramentas à sua disposição para cumprir a instrução. "
                "Seu resultado final será a saída direta da ferramenta que você usar."
            ),
            expected_output="O texto lido de um arquivo, ou uma mensagem de sucesso indicando o ID do documento gerado.",
            agent=agente_executor_de_arquivos,
            context=[tarefa_de_analise_e_planejamento]
        )

        # Configura e executa a Crew
        crew = Crew(
            agents=[agente_roteador, agente_executor_de_arquivos, agente_conversador],
            tasks=[tarefa_de_analise_e_planejamento, tarefa_de_execucao, tarefa_de_conversacao],
            process=Process.sequential, # Mesmo sequencial, a delegação do roteador direciona o fluxo.
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