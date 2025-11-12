# src/tasks/llm_fallback.py

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.messages import BaseMessage
from src.config import Config
from src.utils.observability import log_with_context
import google.api_core.exceptions
from typing import Any, List

logger = log_with_context(component="LLMFallback")

class FallbackLLM(Runnable):
    """
    Um Runnable customizado que tenta uma lista de modelos de LLM em sequência.
    Esta versão aprimorada suporta a delegação de métodos como `bind_tools`.
    """
    def __init__(self, temperature: float = 0.7):
        # Cria as instâncias dos LLMs, mas não as armazena diretamente em self.llms
        self.model_names = Config.LLM_MODEL_LIST
        self._llms = [
            ChatGoogleGenerativeAI(
                model=name,
                google_api_key=Config.GOOGLE_API_KEY,
                convert_system_message_to_human=True,
                temperature=temperature
            ) for name in self.model_names
        ]
        
        if not self._llms:
            raise ValueError("A lista de modelos LLM não pode estar vazia.")
            
        # A lista de runnables que serão tentados em sequência
        self.runnables = self._llms

    def bind_tools(self, *args, **kwargs) -> "FallbackLLM":
        """
        Aplica o .bind_tools() a cada LLM interno na nossa lista de fallback.
        Retorna a própria instância para permitir o encadeamento (chaining).
        """
        # Cria uma nova lista de runnables, cada um com as ferramentas vinculadas
        self.runnables = [llm.bind_tools(*args, **kwargs) for llm in self._llms]
        return self

    def invoke(self, messages: list[BaseMessage], config: RunnableConfig = None, **kwargs) -> BaseMessage:
        """
        Tenta invocar os runnables (que podem ser LLMs simples ou LLMs com ferramentas vinculadas)
        em ordem. Retorna o resultado do primeiro que for bem-sucedido.
        """
        recoverable_errors = (
            google.api_core.exceptions.ResourceExhausted,
            google.api_core.exceptions.ServiceUnavailable,
            google.api_core.exceptions.InternalServerError,
            google.api_core.exceptions.DeadlineExceeded,
        )

        last_error = None

        # Agora iteramos sobre self.runnables, não self._llms
        for i, runnable in enumerate(self.runnables):
            model_name = self.model_names[i]
            try:
                logger.info(f"Tentando invocar o modelo: {model_name}")
                result = runnable.invoke(messages, config=config, **kwargs)
                
                # Verificação de resposta bloqueada por segurança
                finish_reason = getattr(result, 'response_metadata', {}).get("finish_reason", "UNSPECIFIED")
                if finish_reason == "SAFETY":
                    logger.warning(f"Modelo {model_name} bloqueou a resposta por motivos de segurança.")
                    raise ValueError("A solicitação foi bloqueada pelos filtros de segurança do modelo.")

                logger.info(f"Modelo {model_name} invocado com sucesso.")
                return result
            
            except recoverable_errors as e:
                logger.warning(
                    f"Modelo {model_name} falhou com um erro recuperável ({type(e).__name__}). Tentando o próximo.",
                    error=str(e)
                )
                last_error = e
                continue
            
            except Exception as e:
                logger.error(f"Erro não recuperável com o modelo {model_name}. Abortando.", error=str(e))
                raise e
        
        if last_error:
            raise last_error
        else:
            raise RuntimeError(f"Todos os modelos de fallback falharam: {self.model_names}")