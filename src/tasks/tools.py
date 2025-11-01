# src/tasks/tools.py

"""
Conjunto de Ferramentas (Tools) para a Camada de IA.

Este módulo foi adaptado para o ecossistema LangChain. As ferramentas, que antes
eram classes herdando de `BaseTool` (CrewAI), agora são funções Python padrão
decoradas com o `@tool` de `langchain_core.tools`.

Vantagens desta abordagem:
-   **Simplicidade:** São apenas funções, mais fáceis de chamar e testar.
-   **Clareza:** O decorador `@tool` e o `args_schema` Pydantic criam um
    contrato claro sobre o que cada ferramenta faz e quais argumentos espera.
-   **Reutilização:** Mantivemos 99% da sua lógica interna robusta, apenas
    mudando a forma como ela é empacotada.

Cada ferramenta continua a usar o `ToolResponse` para padronizar as saídas,
garantindo uma comunicação previsível entre as ferramentas e os nós do grafo.
"""

import io
import re
from datetime import datetime
from typing import Dict, Any, Optional

import pandas as pd
import fitz
import json
from bson import ObjectId
from bson.errors import InvalidId
from docx import Document
from docxtpl import DocxTemplate
from jinja2 import Environment, Undefined, exceptions as jinja_exceptions
from pydantic import BaseModel, Field
from langchain_core.tools import tool

from src.db.mongo import get_db, get_gridfs
from src.models.tool_response import ToolResponse, ErrorCodes
from src.tasks.file_generators import criar_docx_stream, criar_xlsx_stream, criar_pdf_stream
from src.utils.docx_placeholders import extract_placeholders_from_docx_bytes
from src.utils.observability import log_with_context, track_performance

logger = log_with_context(component="Tools-LangChain")

# --- Funções Helper (Reutilizadas da sua implementação original) ---

def _to_objectid_if_possible(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return value

def _normalizar_contexto(contexto: Any) -> Any:
    if isinstance(contexto, str):
        return contexto.strip()
    if isinstance(contexto, list):
        return [_normalizar_contexto(item) for item in contexto]
    if isinstance(contexto, dict):
        return {chave: _normalizar_contexto(valor) for chave, valor in contexto.items()}
    return contexto

# ---------------- Definições de Input (Pydantic Schemas) ----------------

class FileReaderInput(BaseModel):
    document_id: str = Field(description="O ID do metadado do documento a ser lido.")

class TemplateFillerInput(BaseModel):
    template_name: str = Field(description="Nome exato do arquivo do template (ex: 'proposta.docx')")
    context: dict = Field(description="Dicionário com chaves/valores para preenchimento")
    owner_id: str = Field(description="ID do usuário dono do novo documento")
    output_filename: Optional[str] = Field(default=None, description="Nome do arquivo a ser gerado (opcional)")

class SimpleDocumentGeneratorInput(BaseModel):
    output_filename: str = Field(description="Nome do arquivo a criar (com extensão .docx, .xlsx, ou .pdf)")
    content: str = Field(description="Conteúdo de texto que será o corpo do documento, separado por novas linhas.")
    owner_id: str = Field(description="ID do usuário dono do novo documento.")

class TemplateInspectorInput(BaseModel):
    template_name: str = Field(description="Nome exato do arquivo do template .docx a ser inspecionado.")

class DatabaseQueryInput(BaseModel):
    document_id: str = Field(description="O ID do metadado do documento a ser consultado.")

# ---------------- Implementação das Ferramentas como Funções Decoradas ----------------

@tool(args_schema=FileReaderInput)
@track_performance
def file_reader_tool(document_id: str) -> Dict[str, Any]:
    """Use para ler o conteúdo de um arquivo DOCX ou XLSX. Forneça o ID do metadado do documento."""
    logger.info("tool_executed", tool="file_reader_tool", document_id=document_id)
    db = get_db()
    fs = get_gridfs()
    try:
        doc_oid = _to_objectid_if_possible(document_id)
        if not isinstance(doc_oid, ObjectId):
            return ToolResponse.error(message=f"ID do documento '{document_id}' é inválido.", error_code=ErrorCodes.INVALID_OBJECT_ID).to_dict()

        doc_meta = db.documents.find_one({"_id": doc_oid})
        if not doc_meta:
            return ToolResponse.error(message=f"Documento com ID '{document_id}' não encontrado.", error_code=ErrorCodes.DOCUMENT_NOT_FOUND).to_dict()

        gridfs_id = doc_meta.get("gridfs_file_id")
        if not gridfs_id:
            return ToolResponse.error(message="Metadado do documento não possui gridfs_file_id.", error_code=ErrorCodes.GRIDFS_ERROR).to_dict()

        gridfs_file = fs.get(_to_objectid_if_possible(gridfs_id))
        filename = doc_meta.get("filename", "").lower()

        if filename.endswith(".docx"):
            doc = Document(io.BytesIO(file_bytes))
            full_text = "\n".join([para.text for para in doc.paragraphs])
            return ToolResponse.success(message="Documento DOCX lido com sucesso", data={"filename": doc_meta.get("filename"), "content": full_text, "content_type": "docx"}).to_dict()
        
        elif filename.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(file_bytes))
            content_str = df.to_string(index=False)
            return ToolResponse.success(message="Planilha Excel lida com sucesso", data={"filename": doc_meta.get("filename"), "content": content_str, "content_type": "excel"}).to_dict()
        
        elif filename.endswith(".pdf"):
            try:
                with fitz.open(stream=file_bytes, filetype="pdf") as doc:
                    full_text = "".join(page.get_text() for page in doc)
                logger.info(f"PDF '{filename}' lido com sucesso. Extraídos {len(full_text)} caracteres.")
                return ToolResponse.success(message="Documento PDF lido com sucesso.", data={"filename": doc_meta.get("filename"), "content": full_text, "content_type": "pdf"}).to_dict()
            except Exception as e:
                logger.error(f"Erro ao processar o arquivo PDF '{filename}'.", error=str(e))
                return ToolResponse.error(message=f"Não foi possível ler o conteúdo do arquivo PDF. Ele pode estar corrompido ou ser baseado em imagem.", error_code=ErrorCodes.VALIDATION_ERROR).to_dict()

        # --- NOVA LÓGICA PARA TXT ---
        elif filename.endswith(".txt"):
            try:
                # Decodifica os bytes para uma string de texto, tentando utf-8 primeiro.
                full_text = file_bytes.decode('utf-8')
                return ToolResponse.success(
                    message="Arquivo de texto (.txt) lido com sucesso.",
                    data={"filename": doc_meta.get("filename"), "content": full_text, "content_type": "text/plain"}
                ).to_dict()
            except UnicodeDecodeError:
                # Fallback para outra codificação comum se utf-8 falhar.
                full_text = file_bytes.decode('latin-1')
                return ToolResponse.success(
                    message="Arquivo de texto (.txt) lido com sucesso (usando codificação latin-1).",
                    data={"filename": doc_meta.get("filename"), "content": full_text, "content_type": "text/plain"}
                ).to_dict()
        
        # --- NOVA LÓGICA PARA CSV ---
        elif filename.endswith(".csv"):
            try:
                # Usa o Pandas para ler o CSV diretamente dos bytes.
                df = pd.read_csv(io.BytesIO(file_bytes))
                content_str = df.to_string(index=False)
                return ToolResponse.success(
                    message="Arquivo CSV lido com sucesso.",
                    data={"filename": doc_meta.get("filename"), "content": content_str, "content_type": "text/csv"}
                ).to_dict()
            except Exception as e:
                logger.error(f"Erro ao processar o arquivo CSV '{filename}'.", error=str(e))
                return ToolResponse.error(message=f"Não foi possível ler o conteúdo do arquivo CSV. Verifique se a formatação está correta.", error_code=ErrorCodes.VALIDATION_ERROR).to_dict()

        # --- NOVA LÓGICA PARA JSON ---
        elif filename.endswith(".json"):
            try:
                # Decodifica os bytes e depois faz o parse do JSON.
                full_text = file_bytes.decode('utf-8')
                json_data = json.loads(full_text)
                # Converte o JSON de volta para uma string formatada (pretty-printed) para o LLM ler.
                content_str = json.dumps(json_data, indent=2, ensure_ascii=False)
                return ToolResponse.success(
                    message="Arquivo JSON lido com sucesso.",
                    data={"filename": doc_meta.get("filename"), "content": content_str, "content_type": "application/json"}
                ).to_dict()
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                logger.error(f"Erro ao processar o arquivo JSON '{filename}'.", error=str(e))
                return ToolResponse.error(message=f"Não foi possível ler o conteúdo do arquivo JSON. Verifique se o arquivo está bem formatado e com codificação UTF-8.", error_code=ErrorCodes.VALIDATION_ERROR).to_dict()
        
        else:
            return ToolResponse.error(message=f"O arquivo '{doc_meta.get('filename')}' não é de um tipo suportado (DOCX, XLSX, PDF, TXT, CSV, JSON).", error_code=ErrorCodes.VALIDATION_ERROR).to_dict()
            
    except Exception as e:
        logger.exception("tool_error", tool="file_reader_tool", error=str(e))
        return ToolResponse.error(message=f"Erro excepcional ao ler o arquivo: {e}", error_code=ErrorCodes.UNKNOWN_ERROR).to_dict()


@tool(args_schema=TemplateFillerInput)
@track_performance
def template_filler_tool(template_name: str, context: dict, owner_id: str, output_filename: Optional[str] = None) -> Dict[str, Any]:
    """Gera um DOCX a partir de um template. Forneça template_name, context, owner_id e opcionalmente output_filename."""
    logger.info("tool_executed", tool="template_filler_tool", template_name=template_name, owner_id=owner_id)
    db, fs = get_db(), get_gridfs()
    try:
        owner_oid = _to_objectid_if_possible(owner_id)
        if not isinstance(owner_oid, ObjectId):
            return ToolResponse.error(message=f"O owner_id '{owner_id}' fornecido não é válido.", error_code=ErrorCodes.INVALID_OBJECT_ID).to_dict()

        template_meta = db.templates.find_one({"filename": template_name})
        if not template_meta:
            return ToolResponse.error(message=f"Template '{template_name}' não encontrado.", error_code=ErrorCodes.TEMPLATE_NOT_FOUND).to_dict()

        gridfs_id = _to_objectid_if_possible(template_meta.get("gridfs_file_id"))
        if not gridfs_id:
            return ToolResponse.error(message="Template sem gridfs_file_id.", error_code=ErrorCodes.GRIDFS_ERROR).to_dict()

        template_file = fs.get(gridfs_id)
        file_bytes = template_file.read()
        
        # --- INÍCIO DA CORREÇÃO ---
        # Antes de renderizar, garantimos que qualquer campo esperado como um loop (coleção)
        # que esteja nulo no JSON da IA seja convertido em uma lista vazia.
        
        # 1. Inspeciona o template para descobrir quais chaves são para loops.
        placeholders_info = extract_placeholders_from_docx_bytes(file_bytes)
        collections_expected = placeholders_info.get("collections", [])
        
        # 2. Normaliza e sanitiza o contexto recebido da IA.
        contexto_sanitizado = _normalizar_contexto(context) or {}
        for collection_name in collections_expected:
            # Se uma chave esperada para um loop for None (nula), forçamos que ela seja uma lista vazia.
            if contexto_sanitizado.get(collection_name) is None:
                logger.warning(
                    f"O campo de coleção '{collection_name}' estava nulo no JSON da IA. "
                    f"Convertendo para lista vazia [] para evitar erro de renderização."
                )
                contexto_sanitizado[collection_name] = []
        # --- FIM DA CORREÇÃO ---

        try:
            doc = DocxTemplate(io.BytesIO(file_bytes))
            env = Environment(undefined=Undefined)
            # Usa o contexto sanitizado, que é seguro para loops.
            doc.render(contexto_sanitizado, jinja_env=env)
            final_doc_stream = io.BytesIO()
            doc.save(final_doc_stream)
            final_doc_stream.seek(0)
        except jinja_exceptions.UndefinedError as ue:
            missing_msg = str(ue)
            m = re.search(r"'([a-zA-Z0-9_\.]+)'\s+is undefined", missing_msg)
            missing_field = m.group(1) if m else None
            return ToolResponse.error(message="Campos necessários ausentes para renderizar o template.", error_code=ErrorCodes.VALIDATION_ERROR, data={"missing_fields": [missing_field] if missing_field else [], "detail": missing_msg}).to_dict()
        except Exception as e:
            logger.exception("Erro ao renderizar template: %s", e)
            return ToolResponse.error(message=f"Falha ao renderizar o template: {e}", error_code=ErrorCodes.UNKNOWN_ERROR, data={"detail": str(e)}).to_dict()

        if not output_filename:
            timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
            base_name = template_name.rsplit('.', 1)[0]
            output_filename = f"{base_name}_preenchido_{timestamp}.docx"

        output_file_id = fs.put(final_doc_stream.getvalue(), filename=output_filename)
        output_doc_meta = {"filename": output_filename, "gridfs_file_id": output_file_id, "owner_id": owner_oid, "template_used": template_name, "created_at": datetime.utcnow()}
        result = db.documents.insert_one(output_doc_meta)

        return ToolResponse.success(message=f"Documento '{output_filename}' gerado com sucesso.", data={"document_id": str(result.inserted_id), "filename": output_filename, "template_used": template_name}).to_dict()
    except Exception as e:
        logger.exception("tool_error", tool="template_filler_tool", error=str(e))
        return ToolResponse.error(message=f"Erro ao preencher o template: {e}", error_code=ErrorCodes.UNKNOWN_ERROR).to_dict()


@tool(args_schema=SimpleDocumentGeneratorInput)
@track_performance
def simple_document_generator_tool(output_filename: str, content: str, owner_id: str) -> Dict[str, Any]:
    """Cria um arquivo DOCX, XLSX ou PDF a partir de um texto simples."""
    logger.info("tool_executed", tool="simple_document_generator_tool", output_filename=output_filename)
    db, fs = get_db(), get_gridfs()
    try:
        owner_oid = _to_objectid_if_possible(owner_id)
        if not isinstance(owner_oid, ObjectId):
            return ToolResponse.error(message=f"O owner_id '{owner_id}' fornecido não é válido.", error_code=ErrorCodes.INVALID_OBJECT_ID).to_dict()

        topicos = [linha.strip() for linha in content.split("\n") if linha.strip()]
        file_format = output_filename.split(".")[-1].lower()

        stream_generators = {"docx": criar_docx_stream, "xlsx": criar_xlsx_stream, "pdf": criar_pdf_stream}
        generator = stream_generators.get(file_format)
        if not generator:
            return ToolResponse.error(message=f"Formato '{file_format}' não suportado. Use 'docx', 'xlsx' ou 'pdf'.", error_code=ErrorCodes.VALIDATION_ERROR).to_dict()

        arquivo_stream = generator(topicos, filename=output_filename)
        output_file_id = fs.put(arquivo_stream.getvalue(), filename=output_filename)

        output_doc_meta = {"filename": output_filename, "gridfs_file_id": output_file_id, "owner_id": owner_oid, "created_at": datetime.utcnow()}
        result = db.documents.insert_one(output_doc_meta)

        return ToolResponse.success(message=f"Documento '{output_filename}' gerado com sucesso.", data={"document_id": str(result.inserted_id), "filename": output_filename, "format": file_format}).to_dict()
    except Exception as e:
        logger.exception("tool_error", tool="simple_document_generator_tool", error=str(e))
        return ToolResponse.error(message=f"Erro ao gerar documento simples: {e}", error_code=ErrorCodes.UNKNOWN_ERROR).to_dict()


@tool(args_schema=TemplateInspectorInput)
@track_performance
def template_inspector_tool(template_name: str) -> Dict[str, Any]:
    """Lê um template .docx e extrai placeholders (variáveis e coleções) que ele espera."""
    logger.info("tool_executed", tool="template_inspector_tool", template_name=template_name)
    db, fs = get_db(), get_gridfs()
    try:
        template_meta = db.templates.find_one({"filename": template_name})
        if not template_meta:
            return ToolResponse.error(message=f"Template '{template_name}' não encontrado.", error_code=ErrorCodes.TEMPLATE_NOT_FOUND, data={"searched_name": template_name}).to_dict()

        gridfs_id = template_meta.get("gridfs_file_id")
        if not gridfs_id:
            return ToolResponse.error(message="Template não tem gridfs_file_id.", error_code=ErrorCodes.GRIDFS_ERROR).to_dict()

        file_bytes = fs.get(_to_objectid_if_possible(gridfs_id)).read()
        placeholders_info = extract_placeholders_from_docx_bytes(file_bytes)

        return ToolResponse.success(
            message=f"Inspeção concluída para {template_name}",
            data={
                "template_name": template_name,
                "all_required": placeholders_info.get("all_required", []),
                "collections": placeholders_info.get("collections", []),
                "variables": placeholders_info.get("variables", []),
            }
        ).to_dict()

    except Exception as e:
        logger.exception("template_inspection_error", template=template_name, error=str(e))
        return ToolResponse.error(
            message=f"Erro ao inspecionar o template: {e}",
            error_code=ErrorCodes.UNKNOWN_ERROR,
            data={"template_name": template_name}
        ).to_dict()
    except Exception as e:
        logger.exception("template_inspection_error", template=template_name, error=str(e))
        return ToolResponse.error(message=f"Erro ao inspecionar o template: {e}", error_code=ErrorCodes.UNKNOWN_ERROR).to_dict()


@tool
@track_performance
def template_lister_tool() -> Dict[str, Any]:
    """Obtém uma lista com os nomes de todos os templates disponíveis no sistema."""
    logger.info("tool_executed", tool="template_lister_tool")
    db = get_db()
    try:
        templates_cursor = db.templates.find({}, {"filename": 1, "_id": 0})
        nomes_templates = [t["filename"] for t in templates_cursor]
        return ToolResponse.success(message=f"Encontrados {len(nomes_templates)} templates no sistema.", data={"templates": nomes_templates}).to_dict()
    except Exception as e:
        logger.exception("tool_error", tool="template_lister_tool", error=str(e))
        return ToolResponse.error(message=f"Erro ao listar os templates: {e}", error_code=ErrorCodes.UNKNOWN_ERROR).to_dict()


@tool(args_schema=DatabaseQueryInput)
@track_performance
def database_query_tool(document_id: str) -> Dict[str, Any]:
    """Consulta metadados sobre um documento específico no banco de dados."""
    logger.info("tool_executed", tool="database_query_tool", document_id=document_id)
    db = get_db()
    try:
        doc_oid = _to_objectid_if_possible(document_id)
        if not isinstance(doc_oid, ObjectId):
            return ToolResponse.error(message=f"ID '{document_id}' inválido.", error_code=ErrorCodes.INVALID_OBJECT_ID).to_dict()

        doc_meta = db.documents.find_one({"_id": doc_oid})
        if not doc_meta:
            return ToolResponse.error(message=f"Nenhum documento encontrado com o ID {document_id}.", error_code=ErrorCodes.DOCUMENT_NOT_FOUND).to_dict()

        # Prepara os dados para serialização
        for key, value in doc_meta.items():
            if isinstance(value, ObjectId):
                doc_meta[key] = str(value)
            if isinstance(value, datetime):
                doc_meta[key] = value.isoformat()

        return ToolResponse.success(message="Metadados do documento recuperados com sucesso.", data={"metadata": doc_meta}).to_dict()
    except Exception as e:
        logger.exception("tool_error", tool="database_query_tool", error=str(e))
        return ToolResponse.error(message=f"Erro ao consultar o banco de dados: {e}", error_code=ErrorCodes.UNKNOWN_ERROR).to_dict()