# Arquivo: /src/tasks/file_generators.py

import io
from docx import Document
from openpyxl import Workbook
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

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