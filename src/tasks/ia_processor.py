# Arquivo: /src/tasks/ia_processor.py

import google.generativeai as genai
from docx import Document
from openpyxl import Workbook
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import io
from src.config import Config
from src.db.mongo import get_db, get_gridfs
from bson import ObjectId
from datetime import datetime

# Configuração da API do Gemini
if not Config.GOOGLE_API_KEY:
    raise ValueError("A variável de ambiente GOOGLE_API_KEY não foi definida.")
genai.configure(api_key=Config.GOOGLE_API_KEY)


def gerar_resposta(prompt: str) -> str:
    """Interage com o Gemini e retorna a resposta em texto."""
    modelo = genai.GenerativeModel("gemini-1.5-flash")
    resposta = modelo.generate_content(prompt)
    return resposta.text

def extrair_topicos(texto: str) -> list:
    """Extrai os tópicos da resposta para formatação."""
    return [linha.strip() for linha in texto.split("\n") if linha.strip()]

def criar_docx_stream(topicos: list) -> io.BytesIO:
    """Cria um arquivo DOCX em memória e retorna seu stream de bytes."""
    doc = Document()
    doc.add_heading("Relatório Gerado por IA", 0)
    for topico in topicos:
        doc.add_paragraph(topico)
    stream = io.BytesIO()
    doc.save(stream)
    stream.seek(0)
    return stream

def criar_xlsx_stream(topicos: list) -> io.BytesIO:
    """Cria um arquivo XLSX em memória e retorna seu stream de bytes."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Relatório IA"
    for i, topico in enumerate(topicos, start=1):
        ws[f"A{i}"] = topico
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return stream

def criar_pdf_stream(topicos: list) -> io.BytesIO:
    """Cria um arquivo PDF em memória e retorna seu stream de bytes."""
    stream = io.BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=letter)
    styles = getSampleStyleSheet()
    flowables = [Paragraph("Relatório Gerado por IA", styles['Heading1']), Spacer(1, 12)]
    
    for topico in topicos:
        flowables.append(Paragraph(topico, styles['Normal']))
        flowables.append(Spacer(1, 6))
        
    doc.build(flowables)
    stream.seek(0)
    return stream


# Esta não é mais uma tarefa do Celery, é uma função Python padrão.
def processar_solicitacao_ia(message_id: str) -> str:
    """
    Executa o fluxo completo de processamento de IA para uma dada mensagem.
    Retorna "Sucesso" ou "Falha".
    """
    print(f"Iniciando processamento para a mensagem: {message_id}")
    db = get_db()
    fs = get_gridfs()
    
    try:
        message = db.messages.find_one({"_id": ObjectId(message_id)})
        if not message:
            print(f"ERRO: Mensagem com ID {message_id} não encontrada.")
            return "Falha"

        prompt = message.get("content")

        # 1. Chamar a IA para gerar a resposta
        resposta_bruta = gerar_resposta(prompt)
        topicos = extrair_topicos(resposta_bruta)
        
        # 2. Criar o arquivo em memória (exemplo com DOCX)
        # TODO: Adicionar lógica para escolher o formato com base no prompt do usuário
        nome_arquivo = "relatorio_gerado.docx"
        arquivo_stream = criar_docx_stream(topicos)
        
        # 3. Salvar o arquivo de saída no GridFS
        output_file_id = fs.put(arquivo_stream, filename=nome_arquivo)
        print(f"Arquivo salvo no GridFS com ID: {output_file_id}")
        
        # 4. Criar o metadado do arquivo de saída na coleção 'documents'
        output_doc_meta = {
            "filename": nome_arquivo,
            "gridfs_file_id": output_file_id,
            "owner_id": message.get("user_id"),
            "created_at": datetime.utcnow()
        }
        output_doc = db.documents.insert_one(output_doc_meta)
        
        # 5. Criar a mensagem de resposta do assistente no chat
        assistant_message = {
            "conversation_id": message.get("conversation_id"),
            "role": "assistant",
            "content": f"Seu documento '{nome_arquivo}' foi gerado com sucesso.",
            "generated_document_id": output_doc.inserted_id,
            "user_id": message.get("user_id"),
            "timestamp": datetime.utcnow()
        }
        db.messages.insert_one(assistant_message)
        
        # 6. Atualizar a conversa para que ela apareça no topo do histórico
        db.conversations.update_one(
            {"_id": message.get("conversation_id")},
            {"$set": {"last_updated_at": datetime.utcnow()}}
        )

        print(f"Processamento para a mensagem {message_id} concluído com sucesso.")
        return "Sucesso"

    except Exception as e:
        print(f"ERRO CRÍTICO ao processar a mensagem {message_id}: {e}")
        # Salva uma mensagem de erro no banco de dados para o usuário ver.
        db.messages.insert_one({
            "conversation_id": message.get("conversation_id"),
            "role": "assistant",
            "content": f"Ocorreu um erro ao processar sua solicitação. Por favor, tente novamente.",
            "user_id": message.get("user_id"),
            "timestamp": datetime.utcnow(),
            "is_error": True
        })
        return "Falha"