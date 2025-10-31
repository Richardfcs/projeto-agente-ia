# src/utils/docx_placeholders.py
from zipfile import ZipFile
import io, re, logging

logger = logging.getLogger(__name__)

DOCX_TEXT_PARTS = [
    'word/document.xml',
    *[f'word/header{n}.xml' for n in range(1, 10)],
    *[f'word/footer{n}.xml' for n in range(1, 10)],
    'word/endnotes.xml',
    'word/footnotes.xml',
    'word/comments.xml',
]

def _extract_tokens_from_xml(xml_text: str):
    tokens = []
    tokens.extend(re.findall(r"<w:t[^>]*>(.*?)</w:t>", xml_text, flags=re.DOTALL))
    tokens.extend(re.findall(r"<w:instrText[^>]*>(.*?)</w:instrText>", xml_text, flags=re.DOTALL))
    tokens.extend(re.findall(r"<w:fldSimple[^>]*>(.*?)</w:fldSimple>", xml_text, flags=re.DOTALL))
    return [tok if tok is not None else "" for tok in tokens]

def extract_placeholders_from_docx_bytes(file_bytes: bytes):
    """
    Versão aprimorada que identifica corretamente coleções e ignora
    variáveis de loop aninhadas do escopo global.
    """
    try:
        # ... (lógica para extrair texto do xml, sem alterações) ...
        with ZipFile(io.BytesIO(file_bytes)) as z:
            names = z.namelist()
            all_tokens = []
            for part in DOCX_TEXT_PARTS:
                if part in names:
                    xml = z.read(part).decode('utf-8', errors='ignore')
                    all_tokens.extend(_extract_tokens_from_xml(xml))
    except Exception as e:
        logger.exception("Erro ao abrir DOCX bytes: %s", e)
        raise

    full_text = ''.join(all_tokens)

    # 1. Encontra todos os loops `for var in collection`
    for_matches = re.findall(r'\{%\s*for\s+([a-zA-Z_][\w]*)\s+in\s+([a-zA-Z_][\w]*)\s*%\}', full_text)
    loop_variables = {var for var, coll in for_matches}
    collections = {coll for var, coll in for_matches}

    # 2. Encontra todas as variáveis `{{ var }}` ou `{{ var.attr }}`
    var_matches = re.findall(r'\{\{\s*([a-zA-Z_][\w\.]*)\s*(?:\|[^}]*)?\}\}', full_text)

    # 3. Processa as variáveis encontradas
    simple_vars = set()
    dotted_bases = set()

    for var in var_matches:
        if '.' in var:
            base = var.split('.')[0]
            dotted_bases.add(base)
        else:
            simple_vars.add(var)

    # 4. Lógica de Limpeza:
    # Remove variáveis que são, na verdade, variáveis de loop (ex: 'secao', 'item')
    # do conjunto de variáveis simples e das bases pontuadas.
    required_simple_vars = simple_vars - loop_variables
    required_bases = dotted_bases - loop_variables

    # 5. Monta o resultado final
    all_required = collections | required_simple_vars | required_bases

    return {
        "variables": sorted(list(required_simple_vars)),
        "collections": sorted(list(collections)),
        "all_required": sorted(list(all_required))
    }