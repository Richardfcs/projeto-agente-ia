# src/utils/observability.py
import logging
import sys
import time
import functools
from typing import Callable, Any, Dict
from datetime import datetime
import structlog
import threading

# Configurar structlog
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()  # Saída em JSON para fácil parsing
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

# Criar logger principal
logger = structlog.get_logger()

def setup_logging(level=logging.INFO):
    """Configura o logging para a aplicação"""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )

def track_performance(func: Callable) -> Callable:
    """Decorator para rastrear performance de funções"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs) -> Any:
        start_time = time.time()
        function_name = func.__name__
        
        logger.info(
            "function_started",
            function=function_name,
            args_count=len(args),
            kwargs_keys=list(kwargs.keys()) if kwargs else []
        )
        
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            
            logger.info(
                "function_completed",
                function=function_name,
                duration_ms=round(duration * 1000, 2),
                success=True
            )
            
            return result
            
        except Exception as e:
            duration = time.time() - start_time
            
            logger.error(
                "function_failed",
                function=function_name,
                duration_ms=round(duration * 1000, 2),
                error=str(e),
                error_type=type(e).__name__,
                success=False
            )
            raise
    
    return wrapper

class CorrelationContext:
    """Gerenciador de contexto para correlation IDs"""
    
    def __init__(self):
        self._storage = threading.local()
    
    def set_correlation_id(self, correlation_id: str):
        self._storage.correlation_id = correlation_id
    
    def get_correlation_id(self) -> str:
        return getattr(self._storage, 'correlation_id', 'unknown')

# Instância global do gerenciador de contexto
correlation_ctx = CorrelationContext()

def log_with_context(**kwargs):
    """Adiciona contexto comum a todos os logs"""
    context = {
        "correlation_id": correlation_ctx.get_correlation_id(),
        "timestamp": datetime.utcnow().isoformat(),
        **kwargs
    }
    return logger.bind(**context)