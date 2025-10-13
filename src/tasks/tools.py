# /src/tasks/tools.py (revisado)
import io
import json
import logging
import re # Importar a biblioteca de regex
from zipfile import ZipFile # Importar ZipFile
from datetime import datetime
from typing import Type, Optional, Dict, Any

import pandas as pd
from bson import ObjectId
from bson.errors import InvalidId
from docx import Document
from docxtpl import DocxTemplate
from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from src.db.mongo import get_db, get_gridfs
from src.tasks.file_generators import criar_docx_stream, criar_xlsx_stream, criar_pdf_stream

logger = logging.getLogger(__name__)

# O tipo de retorno agora é padronizado para ser sempre um dicionário.
ReturnType = Dict[str, Any]

def _to_objectid_if_possible(value: Any) -> Any:
    """Tenta converter string para ObjectId; se já for ObjectId, retorna; senão retorna original."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return value

# --- NOVA FUNÇÃO AUXILIAR DE LIMPEZA ---
def _limpar_contexto_vazio(contexto: Any) -> Any:
    """
    Função recursiva para limpar um dicionário de contexto.
    - Converte strings vazias ou com apenas espaços para None.
    - Remove itens None de listas.
    - Funciona com dicionários, listas, strings e outros tipos.
    """
    if isinstance(contexto, str):
        # Se for uma string e estiver "vazia", retorna None.
        return contexto if contexto.strip() else None
        
    if isinstance(contexto, list):
        # Se for uma lista, processa cada item e filtra os resultados que se tornaram None.
        lista_limpa = [_limpar_contexto_vazio(item) for item in contexto]
        return [item for item in lista_limpa if item is not None]
        
    if isinstance(contexto, dict):
        # Se for um dicionário, processa cada valor.
        # Mantém a chave mesmo que o valor se torne None (Jinja2 lida bem com isso).
        dict_limpo = {}
        for chave, valor in contexto.items():
            dict_limpo[chave] = _limpar_contexto_vazio(valor)
        return dict_limpo
        
    # Para qualquer outro tipo de dado (números, booleanos, etc.), retorna como está.
    return contexto
# --- FIM DA NOVA FUNÇÃO ---

# ---------------- FileReaderTool ----------------
class FileReaderInput(BaseModel):
    document_id: str = Field(description="O ID do metadado do documento a ser lido.")

class FileReaderTool(BaseTool):
    name: str = "Leitor de Arquivos do Usuário"
    description: str = "Use para ler o conteúdo de um arquivo DOCX ou XLSX. Forneça o ID do metadado do documento."
    args_schema: Type[BaseModel] = FileReaderInput

    def _run(self, document_id: str) -> ReturnType:
        logger.info("FileReaderTool executada com document_id=%s", document_id)
        db = get_db()
        fs = get_gridfs()

        try:
            doc_oid = _to_objectid_if_possible(document_id)
            if not isinstance(doc_oid, ObjectId):
                return {"status": "error", "message": f"ID do documento '{document_id}' é inválido."}

            doc_meta = db.documents.find_one({"_id": doc_oid})
            if not doc_meta:
                return {"status": "error", "message": f"Documento com ID '{document_id}' não encontrado."}

            gridfs_id = doc_meta.get("gridfs_file_id")
            if not gridfs_id:
                return {"status": "error", "message": "Metadado do documento não possui gridfs_file_id."}

            gridfs_oid = _to_objectid_if_possible(gridfs_id)
            gridfs_file = fs.get(gridfs_oid)
            filename = doc_meta.get("filename", "").lower()

            # MELHORIA (Desempenho): Passa o stream do GridFS diretamente para as bibliotecas,
            # evitando carregar o arquivo inteiro na memória desnecessariamente.
            if filename.endswith(".docx"):
                doc = Document(gridfs_file)
                full_text = "\n".join([para.text for para in doc.paragraphs])
                return {"status": "success", "filename": doc_meta.get("filename"), "content": full_text}
            
            elif filename.endswith((".xlsx", ".xls")):
                df = pd.read_excel(gridfs_file, engine="openpyxl")
                return {"status": "success", "filename": doc_meta.get("filename"), "content_markdown": df.to_markdown(index=False)}
            
            else:
                return {"status": "error", "message": f"O arquivo '{doc_meta.get('filename')}' não é de um tipo suportado (DOCX, XLSX)."}

        except Exception as e:
            logger.exception("Erro em FileReaderTool")
            return {"status": "error", "message": f"Erro excepcional ao ler o arquivo: {e}"}

# ---------------- TemplateFillerTool ----------------
class TemplateFillerInput(BaseModel):
    template_name: str = Field(description="Nome exato do arquivo do template (ex: 'proposta.docx')")
    context: dict = Field(description="Dicionário com chaves/valores para preenchimento")
    owner_id: str = Field(description="ID do usuário dono do novo documento")
    output_filename: Optional[str] = Field(default=None, description="Nome do arquivo a ser gerado (opcional)")

class TemplateFillerTool(BaseTool):
    name: str = "Preenchedor de Templates de Documentos"
    description: str = "Gera um DOCX a partir de um template. Forneça template_name, context, owner_id e opcionalmente output_filename."
    args_schema: Type[BaseModel] = TemplateFillerInput

    def _run(self, template_name: str, context: dict, owner_id: str, output_filename: str = None) -> ReturnType:
        logger.info("TemplateFillerTool: template=%s owner=%s", template_name, owner_id)
        db, fs = get_db(), get_gridfs()

        try:
            owner_oid = _to_objectid_if_possible(owner_id)
            if not isinstance(owner_oid, ObjectId):
                return {"status": "error", "message": f"O owner_id '{owner_id}' fornecido não é válido."}

            template_meta = db.templates.find_one({"filename": template_name})
            if not template_meta:
                return {"status": "error", "message": f"Template '{template_name}' não encontrado."}

            gridfs_id = _to_objectid_if_possible(template_meta.get("gridfs_file_id"))
            template_file = fs.get(gridfs_id)

            doc = DocxTemplate(template_file)
            
            # --- INÍCIO DA MUDANÇA ---
            # Antes de renderizar, limpamos o contexto gerado pela IA.
            contexto_limpo = _limpar_contexto_vazio(context)
            logger.info("Contexto após limpeza: %s", contexto_limpo)
            
            doc.render(contexto_limpo)
            # --- FIM DA MUDANÇA ---

            final_doc_stream = io.BytesIO()
            doc.save(final_doc_stream)
            final_doc_stream.seek(0)

            if not output_filename:
                timestamp = datetime.utcnow().strftime("%Y%m%d")
                base_name = template_name.replace('.docx', '').replace('.doc', '')
                output_filename = f"{base_name}_preenchido_{timestamp}.docx"

            output_file_id = fs.put(final_doc_stream.getvalue(), filename=output_filename)
            
            output_doc_meta = {
                "filename": output_filename, "gridfs_file_id": output_file_id,
                "owner_id": owner_oid, "created_at": datetime.utcnow()
            }
            result = db.documents.insert_one(output_doc_meta)

            return {"status": "success", "message": f"Documento '{output_filename}' gerado com sucesso.", "document_id": str(result.inserted_id)}

        except Exception as e:
            logger.exception("Erro excepcional em TemplateFillerTool")
            return {"status": "error", "message": f"Erro excepcional ao preencher o template: {e}"}

# ---------------- SimpleDocumentGeneratorTool ----------------
class SimpleDocumentGeneratorInput(BaseModel):
    output_filename: str = Field(description="Nome do arquivo a criar (com extensão .docx, .xlsx, ou .pdf)")
    content: str = Field(description="Conteúdo de texto que será o corpo do documento, separado por novas linhas.")
    owner_id: str = Field(description="ID do usuário dono do novo documento.")

class SimpleDocumentGeneratorTool(BaseTool):
    name: str = "Gerador de Documentos Simples"
    description: str = "Cria um arquivo DOCX, XLSX ou PDF a partir de um texto simples."
    args_schema: Type[BaseModel] = SimpleDocumentGeneratorInput

    def _run(self, output_filename: str, content: str, owner_id: str) -> ReturnType:
        logger.info("SimpleDocumentGeneratorTool criando: %s", output_filename)
        db, fs = get_db(), get_gridfs()

        try:
            owner_oid = _to_objectid_if_possible(owner_id)
            if not isinstance(owner_oid, ObjectId):
                return {"status": "error", "message": f"O owner_id '{owner_id}' fornecido não é válido."}

            topicos = [linha.strip() for linha in content.split("\n") if linha.strip()]
            file_format = output_filename.split(".")[-1].lower()

            stream_generators = {
                "docx": criar_docx_stream,
                "xlsx": criar_xlsx_stream,
                "pdf": criar_pdf_stream,
            }
            generator = stream_generators.get(file_format)
            if not generator:
                return {"status": "error", "message": f"Formato '{file_format}' não suportado. Use 'docx', 'xlsx' ou 'pdf'."}

            arquivo_stream = generator(topicos)
            arquivo_stream.seek(0)
            
            output_file_id = fs.put(arquivo_stream.getvalue(), filename=output_filename)

            output_doc_meta = {
                "filename": output_filename, "gridfs_file_id": output_file_id,
                "owner_id": owner_oid, "created_at": datetime.utcnow()
            }
            result = db.documents.insert_one(output_doc_meta)

            return {"status": "success", "message": f"Documento '{output_filename}' gerado com sucesso.", "document_id": str(result.inserted_id)}
        except Exception as e:
            logger.exception("Erro em SimpleDocumentGeneratorTool")
            return {"status": "error", "message": f"Erro excepcional ao gerar documento simples: {e}"}

# ---------------- TemplateInspectorTool ----------------
class TemplateInspectorInput(BaseModel):
    template_name: str = Field(description="Nome exato do arquivo do template .docx a ser inspecionado.")

class TemplateInspectorTool(BaseTool):
    name: str = "Inspetor de Placeholders de Template"
    description: str = "Lê um template .docx e extrai uma lista de todos os placeholders (variáveis Jinja2) que ele espera."
    args_schema: Type[BaseModel] = TemplateInspectorInput

    # --- INÍCIO DA MUDANÇA (CORREÇÃO DA REGEX) ---
    def _run(self, template_name: str) -> ReturnType:
        logger.info(f"TemplateInspectorTool executando para: {template_name}")
        db, fs = get_db(), get_gridfs()

        template_meta = db.templates.find_one({"filename": template_name})
        if not template_meta:
            return {"status": "error", "message": f"Template '{template_name}' não encontrado."}

        try:
            gridfs_file = fs.get(template_meta["gridfs_file_id"])

            with ZipFile(io.BytesIO(gridfs_file.read())) as docx_zip:
                xml_content = docx_zip.read('word/document.xml').decode('utf-8')

            # REGEX CORRIGIDA: Esta regex é muito mais específica para o formato Jinja2.
            # Ela procura por {{ var }} ou {% comando %} e extrai apenas o conteúdo limpo.
            jinja_blocks = re.findall(r'\{\{.*?\}\}|\{%.*?%\}', xml_content)
            
            variaveis = set()
            for block in jinja_blocks:
                # Limpa os caracteres especiais e pega a primeira parte (antes de filtros como | tojson)
                cleaned_block = re.sub(r'[\{\}\%\s]', '', block).split('|')[0].split('.')[0]
                # Ignora palavras-chave de controle do Jinja2
                if cleaned_block and cleaned_block not in ['if', 'for', 'in', 'endif', 'endfor']:
                    variaveis.add(cleaned_block)

            if not variaveis:
                return {"status": "success", "variables": [], "message": "Nenhum placeholder encontrado."}

            # A mudança principal: retornar um JSON estruturado, como o resto do sistema espera.
            return {"status": "success", "variables": sorted(list(variaveis))}
        except Exception as e:
            logger.exception("Erro ao inspecionar o template com regex.")
            return {"status": "error", "message": f"Erro ao inspecionar o template: {e}"}
            
# ---------------- TemplateListerTool ----------------
class TemplateListerTool(BaseTool):
    name: str = "Listador de Templates Disponíveis"
    description: str = "Obtém uma lista com os nomes de todos os templates disponíveis no sistema."
    
    def _run(self) -> ReturnType:
        logger.info("TemplateListerTool executada")
        db = get_db()
        try:
            # A query com projeção já estava perfeita.
            templates_cursor = db.templates.find({}, {"filename": 1, "_id": 0})
            nomes_templates = [t["filename"] for t in templates_cursor]
            
            return {"status": "success", "templates": nomes_templates}
        except Exception as e:
            logger.exception("Erro ao listar templates")
            return {"status": "error", "message": f"Erro ao listar os templates: {e}"}

# NOTA: A DatabaseQueryTool foi omitida por ser primariamente para depuração/metadados.
# Se for usada pelos agentes, pode ser mantida e refatorada da mesma forma.
# ---------------- DatabaseQueryTool ----------------
class DatabaseQueryInput(BaseModel):
    document_id: str = Field(description="O ID do metadado do documento a ser consultado.")


class DatabaseQueryTool(BaseTool):
    name: str = "Consultor de Banco de Dados de Documentos"
    description: str = "Consulta metadados sobre documentos. Forneça document_id."
    args_schema: Type[BaseModel] = DatabaseQueryInput

    def _run(self, document_id: str) -> str:
        logger.info("DatabaseQueryTool executada com document_id=%s", document_id)
        db = get_db()
        try:
            try:
                doc_oid = ObjectId(document_id)
            except InvalidId:
                return f"Erro: ID '{document_id}' inválido."

            doc_meta = db.documents.find_one({"_id": doc_oid})
            if not doc_meta:
                return f"Erro: Nenhum documento encontrado com o ID {document_id}."

            doc_meta.pop("_id", None)
            doc_meta["gridfs_file_id"] = str(doc_meta.get("gridfs_file_id"))
            doc_meta["owner_id"] = str(doc_meta.get("owner_id"))
            return {"status": "success", "metadata": doc_meta}
        except Exception as e:
            logger.exception("Erro em DatabaseQueryTool")
            return f"Erro ao consultar o banco de dados: {e}"