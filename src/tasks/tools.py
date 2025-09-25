# Arquivo: /src/tasks/tools.py

import io
import pandas as pd
from datetime import datetime
from bson import ObjectId
from docx import Document
from docxtpl import DocxTemplate
from crewai.tools import BaseTool
from src.db.mongo import get_db, get_gridfs
from src.tasks.file_generators import criar_docx_stream, criar_xlsx_stream, criar_pdf_stream

class FileReaderTool(BaseTool):
    name: str = "Leitor de Arquivos do Usuário"
    description: str = "Use esta ferramenta para ler o conteúdo de um arquivo DOCX ou XLSX que o usuário anexou. Você deve fornecer o ID do metadado do documento (document_id) que está na conversa."

    def _run(self, document_id: str) -> str:
        """Lê o conteúdo de um arquivo do GridFS."""
        print(f"--- Ferramenta FileReaderTool executada com document_id: {document_id} ---")
        db = get_db()
        fs = get_gridfs()
        
        try:
            doc_meta = db.documents.find_one({"_id": ObjectId(document_id)})
            if not doc_meta:
                return "Erro: Documento com o ID fornecido não foi encontrado."
            
            gridfs_id = doc_meta["gridfs_file_id"]
            gridfs_file = fs.get(gridfs_id)
            file_stream = io.BytesIO(gridfs_file.read())

            if doc_meta["filename"].endswith(".docx"):
                doc = Document(file_stream)
                full_text = "\n".join([para.text for para in doc.paragraphs])
                return f"Conteúdo do arquivo '{doc_meta['filename']}':\n{full_text}"
            
            elif doc_meta["filename"].endswith((".xlsx", ".xls")):
                df = pd.read_excel(file_stream)
                return f"Conteúdo da planilha '{doc_meta['filename']}' em formato Markdown:\n{df.to_markdown(index=False)}"
            
            else:
                return f"Erro: O arquivo '{doc_meta['filename']}' não é de um tipo suportado (DOCX, XLSX)."
        except Exception as e:
            return f"Erro excepcional ao tentar ler o arquivo: {e}"

class TemplateFillerTool(BaseTool):
    name: str = "Preenchedor de Templates de Documentos"
    description: str = "Use esta ferramenta para gerar um novo documento DOCX a partir de um template existente. Você precisa fornecer o nome do arquivo do template (ex: 'proposta.docx'), o ID do usuário dono do novo documento (owner_id), e um dicionário JSON com os dados de contexto para preenchimento."

    def _run(self, template_name: str, context: dict, owner_id: str) -> str:
        """
        Preenche um template do GridFS com o contexto fornecido, salva o novo
        arquivo associado ao owner_id e retorna o ID do novo documento.
        """
        print(f"--- Ferramenta TemplateFillerTool executada para o template: {template_name} por owner: {owner_id} ---")

        # Validação crucial dos parâmetros
        if not all([template_name, context, owner_id]):
            return "Erro: A ferramenta 'Preenchedor de Templates' requer os parâmetros 'template_name', 'context', e 'owner_id'."

        db = get_db()
        fs = get_gridfs()

        try:
            template_meta = db.templates.find_one({"filename": template_name})
            if not template_meta:
                return f"Erro: O template chamado '{template_name}' não foi encontrado no sistema."
            
            template_file = fs.get(template_meta["gridfs_file_id"])
            template_stream = io.BytesIO(template_file.read())
            
            doc = DocxTemplate(template_stream)
            doc.render(context)
            
            final_doc_stream = io.BytesIO()
            doc.save(final_doc_stream)
            final_doc_stream.seek(0)
            
            # Salva o novo documento no GridFS
            novo_nome_arquivo = f"Documento_de_{template_name.replace('.docx', '')}.docx"
            output_file_id = fs.put(final_doc_stream, filename=novo_nome_arquivo)
            
            # Cria o metadado do novo documento
            output_doc_meta = {
                "filename": novo_nome_arquivo,
                "gridfs_file_id": output_file_id,
                "owner_id": ObjectId(owner_id), # Usa o owner_id recebido
                "created_at": datetime.utcnow()
            }
            output_doc = db.documents.insert_one(output_doc_meta)

            return f"Documento gerado com sucesso a partir do template '{template_name}'. O ID do metadado do novo documento é: {str(output_doc.inserted_id)}"
        
        except InvalidId:
            return f"Erro: O owner_id '{owner_id}' fornecido não é um ID válido."
        except Exception as e:
            return f"Erro excepcional ao tentar preencher o template: {e}"

class SimpleDocumentGeneratorTool(BaseTool):
    name: str = "Gerador de Documentos Simples"
    description: str = "Use esta ferramenta para criar um novo documento (DOCX, XLSX ou PDF) a partir de um bloco de texto. Você precisa fornecer o nome do arquivo de saída (output_filename), o conteúdo de texto (content) e o ID do usuário dono (owner_id)."

    def _run(self, output_filename: str, content: str, owner_id: str) -> str:
        """
        Cria um arquivo em um formato especificado a partir de um texto,
        salva-o no GridFS e retorna o ID do novo documento.
        """
        print(f"--- Ferramenta SimpleDocumentGeneratorTool executada para criar: {output_filename} ---")
        
        if not all([output_filename, content, owner_id]):
            return "Erro: A ferramenta 'Gerador de Documentos Simples' requer os parâmetros 'output_filename', 'content' e 'owner_id'."
            
        db = get_db()
        fs = get_gridfs()
        
        # Converte o conteúdo de texto para uma lista de "tópicos"
        # que nossas funções de stream esperam.
        topicos = [linha.strip() for linha in content.split('\n') if linha.strip()]
        
        # Lógica para escolher a função de criação de stream correta
        file_format = output_filename.split('.')[-1].lower()
        arquivo_stream = None
        
        if file_format == 'docx':
            arquivo_stream = criar_docx_stream(topicos)
        elif file_format == 'xlsx':
            arquivo_stream = criar_xlsx_stream(topicos)
        elif file_format == 'pdf':
            arquivo_stream = criar_pdf_stream(topicos)
        else:
            return f"Erro: Formato de arquivo '{file_format}' não suportado. Use 'docx', 'xlsx' ou 'pdf'."

        if not arquivo_stream:
            return "Erro: Falha ao gerar o stream do arquivo."

        try:
            # Salva o novo documento no GridFS
            output_file_id = fs.put(arquivo_stream, filename=output_filename)
            
            # Cria o metadado do novo documento
            output_doc_meta = {
                "filename": output_filename,
                "gridfs_file_id": output_file_id,
                "owner_id": ObjectId(owner_id),
                "created_at": datetime.utcnow()
            }
            output_doc = db.documents.insert_one(output_doc_meta)

            return f"Documento '{output_filename}' gerado com sucesso. O ID do metadado do novo documento é: {str(output_doc.inserted_id)}"

        except Exception as e:
            return f"Erro excepcional ao tentar gerar o documento simples: {e}"