# /src/tasks/tools.py (revisado)
import io
import json
import re # Importar a biblioteca de regex
from zipfile import ZipFile # Importar ZipFile
from datetime import datetime
from typing import Type, Optional, Dict, Any
from src.utils.docx_placeholders import extract_placeholders_from_docx_bytes
from jinja2 import Environment, StrictUndefined, exceptions as jinja_exceptions

import pandas as pd
from bson import ObjectId
from bson.errors import InvalidId
from docx import Document
from docxtpl import DocxTemplate
from pydantic import BaseModel, Field
from crewai.tools import BaseTool
from src.db.mongo import get_db, get_gridfs
from src.tasks.file_generators import criar_docx_stream, criar_xlsx_stream, criar_pdf_stream
from src.models.tool_response import ToolResponse, ErrorCodes
from src.utils.observability import log_with_context, track_performance

logger = log_with_context(component="Tools")

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

def _normalizar_contexto(contexto: Any) -> Any:
    """
    Normaliza o contexto para renderização de templates DOCX:
    - Strings: remove espaços extras, mas mantém vazias como "".
    - Listas: mantém todos os itens, normalizando recursivamente.
    - Dicionários: mantém todas as chaves, normalizando valores.
    - Outros tipos: retornam como estão.
    
    Esta versão é mais segura para templates genéricos e não remove campos.
    """
    if isinstance(contexto, str):
        return contexto.strip()  # mantém "" em vez de None

    if isinstance(contexto, list):
        return [_normalizar_contexto(item) for item in contexto]

    if isinstance(contexto, dict):
        return {chave: _normalizar_contexto(valor) for chave, valor in contexto.items()}

    # Tipos numéricos, booleanos ou outros
    return contexto

# ---------------- FileReaderTool ----------------
class FileReaderInput(BaseModel):
    document_id: str = Field(description="O ID do metadado do documento a ser lido.")

# FileReaderTool - VERSÃO ATUALIZADA
class FileReaderTool(BaseTool):
    name: str = "Leitor de Arquivos do Usuário"
    description: str = "Use para ler o conteúdo de um arquivo DOCX ou XLSX. Forneça o ID do metadado do documento."
    args_schema: Type[BaseModel] = FileReaderInput

    @track_performance
    def _run(self, document_id: str) -> ReturnType:
        logger.info("tool_executed", tool="FileReaderTool", document_id=document_id)
        db = get_db()
        fs = get_gridfs()

        try:
            doc_oid = _to_objectid_if_possible(document_id)
            if not isinstance(doc_oid, ObjectId):
                return ToolResponse.error(
                    message=f"ID do documento '{document_id}' é inválido.",
                    error_code=ErrorCodes.INVALID_OBJECT_ID
                ).to_dict()

            doc_meta = db.documents.find_one({"_id": doc_oid})
            if not doc_meta:
                return ToolResponse.error(
                    message=f"Documento com ID '{document_id}' não encontrado.",
                    error_code=ErrorCodes.DOCUMENT_NOT_FOUND
                ).to_dict()

            gridfs_id = doc_meta.get("gridfs_file_id")
            if not gridfs_id:
                return ToolResponse.error(
                    message="Metadado do documento não possui gridfs_file_id.",
                    error_code=ErrorCodes.GRIDFS_ERROR
                ).to_dict()

            gridfs_oid = _to_objectid_if_possible(gridfs_id)
            gridfs_file = fs.get(gridfs_oid)
            filename = doc_meta.get("filename", "").lower()

            # ✅ CORREÇÃO APLICADA: Sempre usar BytesIO
            if filename.endswith(".docx"):
                file_bytes = gridfs_file.read()
                bio = io.BytesIO(file_bytes)
                bio.seek(0)
                doc = Document(bio)
                full_text = "\n".join([para.text for para in doc.paragraphs])
                return ToolResponse.success(
                    message="Documento DOCX lido com sucesso",
                    data={
                        "filename": doc_meta.get("filename"),
                        "content": full_text,
                        "content_type": "docx"
                    }
                ).to_dict()
            
            elif filename.endswith((".xlsx", ".xls")):
                file_bytes = gridfs_file.read()
                bio = io.BytesIO(file_bytes)
                bio.seek(0)
                df = pd.read_excel(bio, engine="openpyxl")
                return ToolResponse.success(
                    message="Planilha Excel lida com sucesso",
                    data={
                        "filename": doc_meta.get("filename"), 
                        "content_markdown": df.to_markdown(index=False),
                        "content_type": "excel"
                    }
                ).to_dict()
            
            else:
                return ToolResponse.error(
                    message=f"O arquivo '{doc_meta.get('filename')}' não é de um tipo suportado (DOCX, XLSX).",
                    error_code=ErrorCodes.VALIDATION_ERROR
                ).to_dict()

        except Exception as e:
            logger.exception("tool_error", tool="FileReaderTool", error=str(e))
            return ToolResponse.error(
                message=f"Erro excepcional ao ler o arquivo: {e}",
                error_code=ErrorCodes.UNKNOWN_ERROR
            ).to_dict()

# ---------------- TemplateFillerTool ----------------
class TemplateFillerInput(BaseModel):
    template_name: str = Field(description="Nome exato do arquivo do template (ex: 'proposta.docx')")
    context: dict = Field(description="Dicionário com chaves/valores para preenchimento")
    owner_id: str = Field(description="ID do usuário dono do novo documento")
    output_filename: Optional[str] = Field(default=None, description="Nome do arquivo a ser gerado (opcional)")

# TemplateFillerTool - VERSÃO ATUALIZADA

class TemplateFillerTool(BaseTool):
    name: str = "Preenchedor de Templates de Documentos"
    description: str = "Gera um DOCX a partir de um template. Forneça template_name, context, owner_id e opcionalmente output_filename."
    args_schema: Type[BaseModel] = TemplateFillerInput

    @track_performance
    def _run(self, template_name: str, context: dict, owner_id: str, output_filename: str = None) -> ReturnType:
        logger.info("tool_executed", tool="TemplateFillerTool", template_name=template_name, owner_id=owner_id)
        db, fs = get_db(), get_gridfs()

        try:
            owner_oid = _to_objectid_if_possible(owner_id)
            if not isinstance(owner_oid, ObjectId):
                return ToolResponse.error(
                    message=f"O owner_id '{owner_id}' fornecido não é válido.",
                    error_code=ErrorCodes.INVALID_OBJECT_ID
                ).to_dict()

            template_meta = db.templates.find_one({"filename": template_name})
            if not template_meta:
                return ToolResponse.error(
                    message=f"Template '{template_name}' não encontrado.",
                    error_code=ErrorCodes.TEMPLATE_NOT_FOUND
                ).to_dict()

            gridfs_id = _to_objectid_if_possible(template_meta.get("gridfs_file_id"))
            if not gridfs_id:
                return ToolResponse.error(
                    message="Template sem gridfs_file_id.",
                    error_code=ErrorCodes.GRIDFS_ERROR
                ).to_dict()

            # Lê os bytes do template
            template_file = fs.get(gridfs_id)
            file_bytes = template_file.read()

            # Extrai placeholders (pode retornar dict ou lista dependendo da versão)
            try:
                placeholders_info = extract_placeholders_from_docx_bytes(file_bytes)
            except Exception as e:
                logger.exception("Erro ao extrair placeholders: %s", e)
                # fallback: tentar continuar sem validação (opcional), aqui preferimos falhar com detalhe
                return ToolResponse.error(
                    message="Falha ao extrair placeholders do template.",
                    error_code=ErrorCodes.UNKNOWN_ERROR,
                    data={"detail": str(e)}
                ).to_dict()

            # --- Compatibilidade: se a função retornou somente uma lista (implementações antigas) ---
            if isinstance(placeholders_info, list):
                required_top_level = list(placeholders_info)
                collections = []
                dotted = []
            elif isinstance(placeholders_info, dict):
                # esperamos chaves: 'all_bases', 'collections', 'dotted', 'variables'
                required_top_level = list(placeholders_info.get("all_bases") or [])
                collections = list(placeholders_info.get("collections") or [])
                dotted = list(placeholders_info.get("dotted") or [])
            else:
                # inesperado
                logger.warning("placeholders_info tem tipo inesperado: %s", type(placeholders_info))
                return ToolResponse.error(
                    message="Formato inesperado do resultado da inspeção do template.",
                    error_code=ErrorCodes.VALIDATION_ERROR,
                    data={"placeholders_info_type": str(type(placeholders_info))}
                ).to_dict()

            # Limpeza do contexto recebido (remove strings vazias, None -> None)
            contexto_limpo = _normalizar_contexto(context) or {}

            # Verifica top-level required
            # missing_top = [k for k in required_top_level if k not in contexto_limpo or contexto_limpo.get(k) in (None, "")]

            # Validação de coleções (se o template espera 'secoes' ou 'dados_coletados', eles devem ser listas)
            collection_type_errors = []
            for col in collections:
                val = contexto_limpo.get(col)
                if val is None:
                    # será coberto por missing_top
                    continue
                if not isinstance(val, list):
                    collection_type_errors.append({"collection": col, "reason": "expected list", "actual_type": type(val).__name__})

            # # Validação básica de itens nas coleções: se temos dotted fields como 'secao.titulo' -> cada item in 'secoes' deve ser dict com 'titulo'
            # nested_key_errors = []
            # for dotted_field in dotted:
            #     if '.' not in dotted_field:
            #         continue
            #     base, subkey = dotted_field.split('.', 1)
            #     if base in contexto_limpo:
            #         val = contexto_limpo.get(base)
            #         if isinstance(val, list) and len(val) > 0:
            #             # verifica apenas o primeiro item (sanity check)
            #             first = val[0]
            #             if not isinstance(first, dict) or subkey not in first:
            #                 nested_key_errors.append({"collection": base, "missing_in_item": subkey})
            #         else:
            #             # se não é lista, será reportado em collection_type_errors or missing_top
            #             pass

            # # Se houver problemas, retorne erro estruturado com expected_structure amostral
            # if missing_top or collection_type_errors or nested_key_errors:
            #     expected_sample = {}
            #     for k in required_top_level:
            #         if k in collections:
            #             expected_sample[k] = [{"...": "..."}]  # indica lista de dicts
            #         else:
            #             expected_sample[k] = "string_or_value_example"

            #     error_data = {
            #         "missing_top_level": missing_top,
            #         "collection_type_errors": collection_type_errors,
            #         "nested_key_errors": nested_key_errors,
            #         "expected_top_level": required_top_level,
            #         "expected_structure_example": expected_sample,
            #         "template": template_name
            #     }
            #     return ToolResponse.error(
            #         message="Campos faltantes ou com formato incorreto para preencher o template.",
            #         error_code=ErrorCodes.VALIDATION_ERROR,
            #         data=error_data
            #     ).to_dict()

            # --- Renderizar ---
            try:
                doc = DocxTemplate(io.BytesIO(file_bytes))
                # tenta render com StrictUndefined para capturar campos faltantes
                env = Environment(undefined=StrictUndefined)
                try:
                    # docxtpl aceita jinja_env kw em versões recentes
                    doc.render(contexto_limpo, jinja_env=env)
                except TypeError:
                    # fallback caso docxtpl não aceite jinja_env
                    # mas ainda queremos capturar UndefinedError: render sem StrictUndefined -> usa env to render string?
                    # prática: pré-validar variáveis antes de render (já fazemos), então chamar doc.render
                    doc.render(contexto_limpo)

                final_doc_stream = io.BytesIO()
                doc.save(final_doc_stream)
                final_doc_stream.seek(0)

            except jinja_exceptions.UndefinedError as ue:
                # captura variáveis faltantes do erro (mensagem vem no formato "'var' is undefined")
                missing_msg = str(ue)
                # tenta extrair o nome da variável
                m = re.search(r"'([a-zA-Z0-9_\.]+)'\s+is undefined", missing_msg)
                missing_field = m.group(1) if m else None
                return ToolResponse.error(
                    message="Campos necessários ausentes para renderizar o template.",
                    error_code=ErrorCodes.VALIDATION_ERROR,
                    data={
                        "missing_fields": [missing_field] if missing_field else [],
                        "detail": missing_msg,
                        "template": template_name
                    }
                ).to_dict()

            except Exception as e:
                logger.exception("Erro ao renderizar template: %s", e)
                return ToolResponse.error(
                    message=f"Falha ao renderizar o template: {e}",
                    error_code=ErrorCodes.UNKNOWN_ERROR,
                    data={"detail": str(e)}
                ).to_dict()


            # salva no GridFS
            if not output_filename:
                from datetime import datetime
                timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
                base_name = template_name.rsplit('.', 1)[0]
                output_filename = f"{base_name}_preenchido_{timestamp}.docx"

            output_file_id = fs.put(final_doc_stream.getvalue(), filename=output_filename)
            output_doc_meta = {
                "filename": output_filename,
                "gridfs_file_id": output_file_id,
                "owner_id": owner_oid,
                "template_used": template_name,
                "created_at": datetime.utcnow()
            }
            result = db.documents.insert_one(output_doc_meta)

            return ToolResponse.success(
                message=f"Documento '{output_filename}' gerado com sucesso.",
                data={
                    "document_id": str(result.inserted_id),
                    "filename": output_filename,
                    "template_used": template_name
                }
            ).to_dict()

        except Exception as e:
            logger.exception("tool_error", tool="TemplateFillerTool", error=str(e))
            return ToolResponse.error(
                message=f"Erro ao preencher o template: {e}",
                error_code=ErrorCodes.UNKNOWN_ERROR
            ).to_dict()

# ---------------- SimpleDocumentGeneratorTool ----------------
class SimpleDocumentGeneratorInput(BaseModel):
    output_filename: str = Field(description="Nome do arquivo a criar (com extensão .docx, .xlsx, ou .pdf)")
    content: str = Field(description="Conteúdo de texto que será o corpo do documento, separado por novas linhas.")
    owner_id: str = Field(description="ID do usuário dono do novo documento.")

# SimpleDocumentGeneratorTool - VERSÃO ATUALIZADA
class SimpleDocumentGeneratorTool(BaseTool):
    name: str = "Gerador de Documentos Simples"
    description: str = "Cria um arquivo DOCX, XLSX ou PDF a partir de um texto simples."
    args_schema: Type[BaseModel] = SimpleDocumentGeneratorInput

    @track_performance
    def _run(self, output_filename: str, content: str, owner_id: str) -> ReturnType:
        logger.info("tool_executed", tool="SimpleDocumentGeneratorTool", output_filename=output_filename)
        db, fs = get_db(), get_gridfs()

        try:
            owner_oid = _to_objectid_if_possible(owner_id)
            if not isinstance(owner_oid, ObjectId):
                return ToolResponse.error(
                    message=f"O owner_id '{owner_id}' fornecido não é válido.",
                    error_code=ErrorCodes.INVALID_OBJECT_ID
                ).to_dict()

            topicos = [linha.strip() for linha in content.split("\n") if linha.strip()]
            file_format = output_filename.split(".")[-1].lower()

            stream_generators = {
                "docx": criar_docx_stream,
                "xlsx": criar_xlsx_stream,
                "pdf": criar_pdf_stream,
            }
            generator = stream_generators.get(file_format)
            if not generator:
                return ToolResponse.error(
                    message=f"Formato '{file_format}' não suportado. Use 'docx', 'xlsx' ou 'pdf'.",
                    error_code=ErrorCodes.VALIDATION_ERROR
                ).to_dict()

            arquivo_stream = generator(topicos, filename=output_filename)
            arquivo_stream.seek(0)
            
            output_file_id = fs.put(arquivo_stream.getvalue(), filename=output_filename)

            output_doc_meta = {
                "filename": output_filename, 
                "gridfs_file_id": output_file_id,
                "owner_id": owner_oid, 
                "created_at": datetime.utcnow()
            }
            result = db.documents.insert_one(output_doc_meta)

            return ToolResponse.success(
                message=f"Documento '{output_filename}' gerado com sucesso.",
                data={
                    "document_id": str(result.inserted_id),
                    "filename": output_filename,
                    "format": file_format,
                    "content_lines": len(topicos)
                }
            ).to_dict()
            
        except Exception as e:
            logger.exception("tool_error", tool="SimpleDocumentGeneratorTool", error=str(e))
            return ToolResponse.error(
                message=f"Erro ao gerar documento simples: {e}",
                error_code=ErrorCodes.UNKNOWN_ERROR
            ).to_dict()

# ---------------- TemplateInspectorTool ----------------
class TemplateInspectorInput(BaseModel):
    template_name: str = Field(description="Nome exato do arquivo do template .docx a ser inspecionado.")

# TemplateInspectorTool - VERSÃO ATUALIZADA
class TemplateInspectorTool(BaseTool):
    name: str = "Inspetor de Placeholders de Template"
    description: str = "Lê um template .docx e extrai placeholders (variáveis e coleções) que ele espera."
    args_schema: Type[BaseModel] = TemplateInspectorInput

    @track_performance
    def _run(self, template_name: str) -> ReturnType:
        logger.info("tool_executed", tool="TemplateInspectorTool", template_name=template_name)
        db, fs = get_db(), get_gridfs()

        template_meta = db.templates.find_one({"filename": template_name})
        if not template_meta:
            return ToolResponse.error(
                message=f"Template '{template_name}' não encontrado.",
                error_code=ErrorCodes.TEMPLATE_NOT_FOUND,
                data={"searched_name": template_name}
            ).to_dict()

        try:
            gridfs_id = template_meta.get("gridfs_file_id")
            if not gridfs_id:
                return ToolResponse.error(
                    message="Template não tem gridfs_file_id.",
                    error_code=ErrorCodes.GRIDFS_ERROR,
                    data={"template_name": template_name}
                ).to_dict()

            file_bytes = fs.get(_to_objectid_if_possible(gridfs_id)).read()

            placeholders_info = extract_placeholders_from_docx_bytes(file_bytes)

            return ToolResponse.success(
                message=f"Inspeção concluída para {template_name}",
                data={
                    "template_name": template_name,
                    "variables_simple": placeholders_info["variables"],
                    "collections": placeholders_info["collections"],
                    "dotted": placeholders_info["dotted"],
                    "required_top_level": placeholders_info["all_bases"],
                    "has_placeholders": bool(placeholders_info["dotted"] or placeholders_info["variables"] or placeholders_info["collections"]),
                }
            ).to_dict()

        except Exception as e:
            logger.exception("template_inspection_error", template=template_name, error=str(e))
            return ToolResponse.error(
                message=f"Erro ao inspecionar o template: {e}",
                error_code=ErrorCodes.UNKNOWN_ERROR,
                data={"template_name": template_name}
            ).to_dict()

# ---------------- TemplateListerTool ----------------
# TemplateListerTool - VERSÃO ATUALIZADA
class TemplateListerTool(BaseTool):
    name: str = "Listador de Templates Disponíveis"
    description: str = "Obtém uma lista com os nomes de todos os templates disponíveis no sistema."
    
    @track_performance
    def _run(self) -> ReturnType:
        logger.info("tool_executed", tool="TemplateListerTool")
        db = get_db()
        try:
            templates_cursor = db.templates.find({}, {"filename": 1, "_id": 0})
            nomes_templates = [t["filename"] for t in templates_cursor]
            
            return ToolResponse.success(
                message=f"Encontrados {len(nomes_templates)} templates no sistema.",
                data={"templates": nomes_templates}
            ).to_dict()
            
        except Exception as e:
            logger.exception("tool_error", tool="TemplateListerTool", error=str(e))
            return ToolResponse.error(
                message=f"Erro ao listar os templates: {e}",
                error_code=ErrorCodes.UNKNOWN_ERROR
            ).to_dict()

# NOTA: A DatabaseQueryTool foi omitida por ser primariamente para depuração/metadados.
# Se for usada pelos agentes, pode ser mantida e refatorada da mesma forma.
# ---------------- DatabaseQueryTool ----------------
class DatabaseQueryInput(BaseModel):
    document_id: str = Field(description="O ID do metadado do documento a ser consultado.")


# DatabaseQueryTool - VERSÃO ATUALIZADA
class DatabaseQueryTool(BaseTool):
    name: str = "Consultor de Banco de Dados de Documentos"
    description: str = "Consulta metadados sobre documentos. Forneça document_id."
    args_schema: Type[BaseModel] = DatabaseQueryInput

    @track_performance
    def _run(self, document_id: str) -> ReturnType:
        logger.info("tool_executed", tool="DatabaseQueryTool", document_id=document_id)
        db = get_db()
        try:
            try:
                doc_oid = ObjectId(document_id)
            except InvalidId:
                return ToolResponse.error(
                    message=f"ID '{document_id}' inválido.",
                    error_code=ErrorCodes.INVALID_OBJECT_ID
                ).to_dict()

            doc_meta = db.documents.find_one({"_id": doc_oid})
            if not doc_meta:
                return ToolResponse.error(
                    message=f"Nenhum documento encontrado com o ID {document_id}.",
                    error_code=ErrorCodes.DOCUMENT_NOT_FOUND
                ).to_dict()

            # Prepara os dados para serialização
            doc_meta_clean = doc_meta.copy()
            doc_meta_clean.pop("_id", None)
            doc_meta_clean["gridfs_file_id"] = str(doc_meta_clean.get("gridfs_file_id"))
            doc_meta_clean["owner_id"] = str(doc_meta_clean.get("owner_id"))
            
            if "created_at" in doc_meta_clean and isinstance(doc_meta_clean["created_at"], datetime):
                doc_meta_clean["created_at"] = doc_meta_clean["created_at"].isoformat()

            return ToolResponse.success(
                message="Metadados do documento recuperados com sucesso.",
                data={"metadata": doc_meta_clean}
            ).to_dict()
            
        except Exception as e:
            logger.exception("tool_error", tool="DatabaseQueryTool", error=str(e))
            return ToolResponse.error(
                message=f"Erro ao consultar o banco de dados: {e}",
                error_code=ErrorCodes.UNKNOWN_ERROR
            ).to_dict()