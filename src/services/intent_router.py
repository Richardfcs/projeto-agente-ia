# src/services/intent_router.py
import re
from typing import List, Dict, Tuple, Optional
from enum import Enum
from src.utils.observability import log_with_context

logger = log_with_context(component="IntentRouter")

class Intent(Enum):
    FILL_TEMPLATE = "PREENCHER_TEMPLATE"
    READ_DOC = "LER_DOCUMENTO" 
    CREATE_DOC = "CRIAR_DOCUMENTO_SIMPLES"
    LIST_TEMPLATES = "LISTAR_TEMPLATES"
    CHAT = "CONVERSA_GERAL"

class HybridIntentRouter:
    """
    Roteador inteligente que usa regex + contexto antes de recorrer ao LLM.
    Elimina 80% das chamadas desnecessárias ao Gemini.
    """
    
    # Padrões de alta confiança (95%+ de precisão)
    PATTERNS = {
        Intent.FILL_TEMPLATE: [
            r"(?:use|usar|preencha|preencher)\s+(?:o\s+)?template\s+['\"]?([\w\d_-]+\.docx?)['\"]?",
            r"template\s+['\"]?([\w\d_-]+\.docx?)['\"]?.*(?:para|com|sobre)",
        ],
        Intent.READ_DOC: [
            r"(?:leia|ler|abrir|abra)\s+(?:o\s+)?(?:arquivo|documento)\s+['\"]?([\w\d_-]+\.\w+)['\"]?",
        ],
        Intent.CREATE_DOC: [
            r"(?:crie|criar|gere|gerar)\s+(?:um\s+)?(?:documento|arquivo|planilha|relat[óo]rio)",
        ],
        Intent.LIST_TEMPLATES: [
            r"(?:liste|listar|mostre|mostrar|quais)\s+.*templates?",
        ]
    }
    
    def route(self, message: str, history: List[Dict]) -> Tuple[Intent, float, Dict]:
        """
        Roteia a mensagem para uma intenção com confiança.
        Mantém a capitalização original dos grupos capturados.
        """
        # NÃO lowercase aqui — usamos flags IGNORECASE nas regex.
        msg = message
        logger.info(f"Roteando mensagem: {message}")

        # FASE 1: Regex de alta confiança (case-insensitive, mas preserva captura original)
        for intent, patterns in self.PATTERNS.items():
            for pattern in patterns:
                match = re.search(pattern, msg, re.IGNORECASE)
                if match:
                    # Preserve the original captured text (as the user typed it)
                    groups = tuple(g for g in match.groups() if g is not None)
                    logger.info(f"Intenção detectada por regex: {intent.value}, groups={groups}")
                    return intent, 0.95, {'matched_groups': groups}

        # FASE 2: Contexto da conversa (última mensagem do assistant)
        if history:
            last_assistant_msg = next(
                (msg for msg in reversed(history) if msg.get('role') == 'assistant'),
                None
            )
            if last_assistant_msg:
                last_assistant_text = last_assistant_msg.get('content', '').lower()
                if 'template' in last_assistant_text:
                    if any(word in msg.lower() for word in ['sim', 'isso', 'correto', 'exato', 'ok', 'use']):
                        logger.info("Intenção detectada por contexto: PREENCHER_TEMPLATE")
                        return Intent.FILL_TEMPLATE, 0.8, {"context_based": True}

        # FASE 3: Keywords simples (confiança média)
        msg_lower = msg.lower()
        if 'template' in msg_lower:
            if any(word in msg_lower for word in ['listar', 'quais', 'mostrar', 'disponíveis']):
                logger.info("Intenção detectada por keyword: LISTAR_TEMPLATES")
                return Intent.LIST_TEMPLATES, 0.7, {}
            logger.info("Intenção detectada por keyword: PREENCHER_TEMPLATE")
            return Intent.FILL_TEMPLATE, 0.6, {"needs_clarification": True}

        if any(word in msg_lower for word in ['ler', 'leia', 'abrir']):
            logger.info("Intenção detectada por keyword: LER_DOCUMENTO")
            return Intent.READ_DOC, 0.6, {}

        if any(word in msg_lower for word in ['criar', 'gerar', 'fazer']):
            logger.info("Intenção detectada por keyword: CRIAR_DOCUMENTO_SIMPLES")
            return Intent.CREATE_DOC, 0.6, {}

        # FASE 4: Fallback - precisa de LLM para classificação
        logger.info("Intenção não detectada, requer LLM: CONVERSA_GERAL")
        return Intent.CHAT, 0.3, {"requires_llm": True}