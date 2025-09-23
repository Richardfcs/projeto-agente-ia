# Arquivo: src/tasks/ia_processor.py

import google.generativeai as genai
from docx import Document
from openpyxl import Workbook
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
import re
import os
import io
from src.config import Config

# --- Configuração da API ---
# Esta configuração será usada pelos workers do Celery
if not Config.GOOGLE_API_KEY:
    raise ValueError("A variável de ambiente GOOGLE_API_KEY não foi definida.")
genai.configure(api_key=Config.GOOGLE_API_KEY)


def gerar_resposta(prompt: str) -> str:
    """Interage com o Gemini e retorna a resposta em texto."""
    modelo = genai.GenerativeModel("gemini-2.5-flash-lite")
    resposta = modelo.generate_content(prompt)
    return resposta.text

def extrair_topicos(texto: str) -> list:
    """Extrai os tópicos da resposta para formatação."""
    # Sua lógica de extração pode ser refinada aqui
    return [linha.strip() for linha in texto.split("\n") if linha.strip()]

# --- Funções adaptadas para retornar streams de dados ---

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