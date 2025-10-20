# src/services/memory_manager.py

import json
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from bson import ObjectId
from src.utils.observability import log_with_context

logger = log_with_context(component="MemoryManager")

class PersistentConversationState:
    """
    Gerencia o estado persistente da conversa no MongoDB.
    Substitui a classe ConversationState original que era volátil.
    """
    
    def __init__(self, db, conversation_id: str):
        self.db = db
        self.conversation_id = conversation_id
        self._state = self._load_state()
    
    def _load_state(self) -> Dict[str, Any]:
        """Carrega o estado do MongoDB. Se não existir, retorna estado vazio."""
        try:
            doc = self.db.conversation_states.find_one({"_id": self.conversation_id})
            if doc:
                logger.info(f"Estado carregado para conversa {self.conversation_id}")
                return doc.get("state", {})
        except Exception as e:
            logger.error(f"Erro ao carregar estado: {e}")
        
        # Estado inicial se não existir no DB
        return {
            "ultimo_template_mencionado": None,
            "ultimo_documento_gerado_id": None,
            "lista_templates_disponiveis": None,
            "contexto_extra_para_llm": ""
        }
    
    def _save_state(self):
        """Salva o estado atual no MongoDB."""
        try:
            self.db.conversation_states.update_one(
                {"_id": self.conversation_id},
                {
                    "$set": {
                        "state": self._state,
                        "updated_at": datetime.utcnow(),
                        "expires_at": datetime.utcnow() + timedelta(hours=24)  # TTL de 24h
                    }
                },
                upsert=True
            )
            logger.debug(f"Estado salvo para conversa {self.conversation_id}")
        except Exception as e:
            logger.error(f"Erro ao salvar estado: {e}")
    
    def update_from_tool_output(self, resultado_crew: str):
        """Atualiza o estado com base na saída JSON de uma ferramenta."""
        try:
            # Tenta parsear se for string, ou usa diretamente se for dict
            if isinstance(resultado_crew, str):
                data = json.loads(resultado_crew)
            else:
                data = resultado_crew
                
            # Agora verifica o formato ToolResponse
            if isinstance(data, dict) and data.get("status") == "success":
                # Extrai dados do campo data se existir
                tool_data = data.get("data", {})
                
                if tool_data.get("templates"):
                    self._state["lista_templates_disponiveis"] = tool_data["templates"]
                    self._state["contexto_extra_para_llm"] += f"\n- Os templates disponíveis no sistema são: {', '.join(tool_data['templates'])}."
                    if len(tool_data["templates"]) == 1:
                        self._state["ultimo_template_mencionado"] = tool_data["templates"][0]
                        self._state["contexto_extra_para_llm"] += f" O template '{self._state['ultimo_template_mencionado']}' foi identificado como contexto principal."
                        
                if tool_data.get("document_id"):
                    self._state["ultimo_documento_gerado_id"] = tool_data["document_id"]
                    
            self._save_state()
        except (json.JSONDecodeError, TypeError, Exception) as e:
            logger.debug(f"Erro ao atualizar estado a partir de tool output: {e}")
    
    def injectar_contexto_no_prompt(self, historico_texto: str) -> str:
        """Adiciona o contexto de estado atual ao histórico que será enviado para os agentes."""
        if not self._state["contexto_extra_para_llm"]:
            return historico_texto
        
        contexto_formatado = "\n\n--- CONTEXTO ATUAL DA CONVERSA (MEMÓRIA DE CURTO PRAZO) ---\n" + self._state["contexto_extra_para_llm"]
        return historico_texto + contexto_formatado

    # Propriedades para acessar os campos do estado de forma direta
    @property
    def ultimo_template_mencionado(self) -> Optional[str]:
        return self._state.get("ultimo_template_mencionado")
    
    @ultimo_template_mencionado.setter
    def ultimo_template_mencionado(self, value: str):
        self._state["ultimo_template_mencionado"] = value
        self._save_state()
    
    @property
    def ultimo_documento_gerado_id(self) -> Optional[str]:
        return self._state.get("ultimo_documento_gerado_id")
    
    @ultimo_documento_gerado_id.setter
    def ultimo_documento_gerado_id(self, value: str):
        self._state["ultimo_documento_gerado_id"] = value
        self._save_state()
    
    @property
    def lista_templates_disponiveis(self) -> Optional[list]:
        return self._state.get("lista_templates_disponiveis")
    
    @lista_templates_disponiveis.setter
    def lista_templates_disponiveis(self, value: list):
        self._state["lista_templates_disponiveis"] = value
        self._save_state()
    
    @property
    def contexto_extra_para_llm(self) -> str:
        return self._state.get("contexto_extra_para_llm", "")
    
    @contexto_extra_para_llm.setter
    def contexto_extra_para_llm(self, value: str):
        self._state["contexto_extra_para_llm"] = value
        self._save_state()

# ADICIONE no final do src/services/memory_manager.py

def init_conversation_states_collection(db):
    """
    Cria índices para a coleção conversation_states.
    Deve ser chamada durante a inicialização da aplicação.
    """
    try:
        # Cria índice TTL no campo expires_at para expirar após 24 horas
        db.conversation_states.create_index("expires_at", expireAfterSeconds=0)
        logger.info("Índice TTL criado para conversation_states.expires_at")
    except Exception as e:
        logger.error(f"Erro ao criar índice TTL: {e}")