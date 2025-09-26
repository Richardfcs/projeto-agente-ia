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

        # Tarefa 1: O Roteador cria o plano de ação.
        tarefa_de_planejamento = Task(
            description=(
                "Você é o gerente de projetos. Sua tarefa é analisar o pedido do usuário e o histórico para criar um plano de ação claro e acionável para um especialista. Siga as regras de decisão abaixo.\n\n"

                "--- INFORMAÇÕES E REGRAS ATUALIZADAS ---\n"
                "1. **Template Principal Disponível:** Existe um template oficial chamado `TEMPLATE_TPF.docx`. Se o usuário pedir um 'relatório de engenharia', 'relatório TPF' ou similar, você deve priorizar o uso deste template.\n\n"
                
                "2. **ESTRUTURA DE DADOS DO TEMPLATE TPF:** Para preencher o `TEMPLATE_TPF.docx`, a ferramenta 'Preenchedor de Templates' precisa de um `context` com a seguinte estrutura JSON. Sua tarefa é extrair as informações do prompt do usuário para preencher o máximo de campos possível. Se uma informação não for fornecida, omita a chave do JSON final.\n"
                "\n"
                "{\n"
                "  \"titulo_documento\": \"(string)\",\n"
                "  \"subtitulo_documento\": \"(string)\",\n"
                "  \"data_documento\": \"(string)\",\n"
                "  \"secao_1_titulo\": \"(string)\",\n"
                "  \"secao_1_conteudo\": \"(string)\",\n"
                "  \"secao_1_sub_1_titulo\": \"(string)\",\n"
                "  \"secao_1_sub_1_conteudo\": \"(string)\",\n"
                "  \"dados_coletados\": [{\"local\": \"(string)\", \"med_A\": \"(valor)\", \"med_B\": \"(valor)\"}],\n"
                "  \"texto_conclusao\": \"(string)\"\n"
                "}\n"
                "\n"
                "3. **Nomeação de Arquivos:** Sempre que uma ferramenta de criação de documento for usada, você DEVE inferir um nome de arquivo descritivo e relevante a partir do contexto (ex: 'inspecao_barragem_norte.docx') e incluí-lo como o parâmetro 'output_filename'.\n\n"

                "--- REGRAS DE DECISÃO (EM ORDEM DE PRIORIDADE) ---\n"
                "A. **VERIFIQUE O HISTÓRICO:** Se a resposta já estiver no histórico, delegue ao 'Especialista em Conversação' com a resposta pronta.\n"
                "B. **USE FERRAMENTAS:** Se a resposta não estiver no histórico, delegue ao 'Especialista em Documentos' com um plano de ação claro, especificando a ferramenta e TODOS os parâmetros necessários.\n"
                "C. **PERGUNTA GERAL:** Se for uma pergunta geral, delegue ao 'Especialista em Conversação'.\n\n"

                f"--- INFORMAÇÕES DISPONÍVEIS PARA A TAREFA ---\n"
                f"- ID do usuário (owner_id): '{user_id}'\n"
                f"- ID do documento anexado: '{input_doc_id}'\n\n"

                f"--- HISTÓRICO DA CONVERSA ---\n{historico_texto}\n--- FIM DO HISTÓRICO ---"
            ),
            expected_output=(
                "Uma instrução final clara e acionável para o especialista apropriado. Se for para preencher o template TPF, "
                "a instrução DEVE conter a chamada para `TemplateFillerTool` com `template_name='TEMPLATE_TPF.docx'` e o `context` "
                "JSON completo com todos os campos extraídos do prompt do usuário."
            ),
            agent=agente_roteador
        )

        # Tarefa 2: O Executor segue o plano que o Roteador criou.
        tarefa_de_execucao = Task(
            description="Execute o plano de ação fornecido pelo Gerente de Projetos.",
            expected_output="O resultado final da execução da ferramenta.",
            agent=agente_executor_de_arquivos,
            context=[tarefa_de_planejamento] # Usa o plano da tarefa anterior como sua instrução.
        )

        # Crew com Processo Sequencial Explícito
        crew = Crew(
            agents=[agente_roteador, agente_executor_de_arquivos],
            tasks=[tarefa_de_planejamento, tarefa_de_execucao],
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