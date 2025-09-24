# Arquivo: /src/tasks/tools.py

import io
import pandas as pd
from datetime import datetime
from bson import ObjectId
from docx import Document
from docxtpl import DocxTemplate
from crewai.tools import BaseTool
from src.db.mongo import get_db, get_gridfs

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
    description: str = "Use esta ferramenta para gerar um novo documento DOCX a partir de um template existente. Você precisa fornecer o nome do template (ex: 'proposta.docx') e um dicionário JSON com os dados de contexto para preenchimento."

    def _run(self, template_name: str, context: dict, owner_id: str) -> str:
        """Preenche um template do GridFS, salva o novo arquivo e retorna seu ID."""
        print(f"--- Ferramenta TemplateFillerTool executada para o template: {template_name} ---")
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
            novo_nome_arquivo = f"Documento_de_{template_name}"
            output_file_id = fs.put(final_doc_stream, filename=novo_nome_arquivo)
            
            # Cria o metadado do novo documento
            output_doc_meta = {
                "filename": novo_nome_arquivo,
                "gridfs_file_id": output_file_id,
                "owner_id": ObjectId(owner_id),
                "created_at": datetime.utcnow()
            }
            output_doc = db.documents.insert_one(output_doc_meta)

            return f"Documento gerado com sucesso a partir do template '{template_name}'. O ID do metadado do novo documento é: {str(output_doc.inserted_id)}"
        except Exception as e:
            return f"Erro excepcional ao tentar preencher o template: {e}"