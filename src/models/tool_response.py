# src/models/tool_response.py
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)

class ToolResponse(BaseModel):
    """
    Modelo padrão para respostas de todas as tools.
    Garante consistência no formato de retorno.
    """
    status: str  # "success" ou "error"
    message: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    error_code: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Converte para dicionário, removendo campos None"""
        return {k: v for k, v in self.dict().items() if v is not None}

    @classmethod
    def success(cls, message: str = None, data: Dict[str, Any] = None) -> 'ToolResponse':
        """Cria uma resposta de sucesso"""
        return cls(status="success", message=message, data=data)

    @classmethod
    def error(cls, message: str, error_code: str = None, data: Dict[str, Any] = None) -> 'ToolResponse':
        """Cria uma resposta de erro"""
        return cls(status="error", message=message, error_code=error_code, data=data)

# Códigos de erro padronizados
class ErrorCodes:
    TEMPLATE_NOT_FOUND = "TEMPLATE_NOT_FOUND"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND" 
    INVALID_OBJECT_ID = "INVALID_OBJECT_ID"
    GRIDFS_ERROR = "GRIDFS_ERROR"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"