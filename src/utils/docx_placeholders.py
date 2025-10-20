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
    Retorna dict com:
      - variables: variáveis simples encontradas (ex: 'titulo_documento')
      - collections: coleções detectadas via {% for var in collection %}
      - dotted: expressões pontuadas (ex: 'secao.titulo')
      - for_map: mapeamento var->collection
      - all_bases: required_top_level (coleções + variables simples + bases substituídas)
    """
    try:
        with ZipFile(io.BytesIO(file_bytes)) as z:
            names = z.namelist()
            all_tokens = []
            for part in DOCX_TEXT_PARTS:
                if part in names:
                    xml = z.read(part).decode('utf-8', errors='ignore')
                    all_tokens.extend(_extract_tokens_from_xml(xml))
            if not all_tokens and 'word/document.xml' in names:
                xml = z.read('word/document.xml').decode('utf-8', errors='ignore')
                all_tokens.extend(_extract_tokens_from_xml(xml))
    except Exception as e:
        logger.exception("Erro ao abrir DOCX bytes: %s", e)
        raise

    # duas junções: sem separador (reconstrói placeholders quebrados) e com espaço (reduz colisão de palavras)
    joined_no_sep = ''.join(all_tokens)
    joined_space = ' '.join(all_tokens)

    # 1) Captura {% for var in collection %} (padrão simples)
    for_matches = re.findall(r'\{%\s*for\s+([a-zA-Z_][\w]*)\s+in\s+([a-zA-Z_][\w]*)\s*%}', joined_no_sep, flags=re.DOTALL)

    # 2) Captura {{ var }} e {{ var.attr }} em joined_no_sep (reconstruído)
    var_matches = re.findall(r'\{\{\s*([a-zA-Z_][\w\.]*)\s*(?:\|[^}]*)?\}\}', joined_no_sep, flags=re.DOTALL)

    dotted = set(var_matches)
    variables = {v for v in dotted if '.' not in v}

    # for_map: var -> collection
    for_map = {var: coll for (var, coll) in for_matches}

    bases_from_dotted = {d.split('.')[0] for d in dotted if '.' in d}

    collections = set(for_map.values())

    # substitui bases que são vars de loop por sua collection, p.ex. 'secao' -> 'secoes'
    substituted_bases = set()
    for b in bases_from_dotted:
        if b in for_map:
            substituted_bases.add(for_map[b])
        else:
            substituted_bases.add(b)

    required_top = collections | variables | substituted_bases

    return {
        "variables": sorted(variables),
        "collections": sorted(collections),
        "dotted": sorted(dotted),
        "for_map": for_map,
        "all_bases": sorted(required_top)
    }
