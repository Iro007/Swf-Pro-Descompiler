"""
Módulo de desofuscación y limpieza para scripts ActionScript decompilados.
Sanea identificadores Unicode corruptos o ilegibles y simplifica expresiones.
"""

import re
from typing import Dict, Tuple


def sanitize_identifier(name: str, symbol_map: Dict[str, str], prefix: str = "sym") -> str:
    """Reemplaza caracteres no imprimibles o nombres de ofuscador por identificadores limpios."""
    if not name:
        return name
    # Verificar si contiene caracteres de control o patrones típicos de ofuscadores (§...§, \\x..)
    is_obfuscated = bool(re.search(r"[\x00-\x1f\x7f-\x9f§]", name)) or (
        name.startswith("_loc") and len(name) > 10
    )

    if not is_obfuscated:
        return name

    if name not in symbol_map:
        clean_name = f"{prefix}_{len(symbol_map) + 1}"
        symbol_map[name] = clean_name
    return symbol_map[name]


def deobfuscate_script(source: str) -> str:
    """Aplica pasadas de desofuscación al código fuente ActionScript."""
    if not source:
        return source

    symbol_map: Dict[str, str] = {}

    # 1. Normalizar variables con §...§ (DoSWF / Amayeta)
    def repl_section_sym(match):
        raw = match.group(0)
        return sanitize_identifier(raw, symbol_map, prefix="var")

    cleaned = re.sub(r"§[^§\n]+§", repl_section_sym, source)

    # 2. Reemplazar caracteres no imprimibles escapados o crudos en identificadores
    cleaned = re.sub(r"\\u[0-9a-fA-F]{4}", lambda m: sanitize_identifier(m.group(0), symbol_map, prefix="id"), cleaned)

    # 3. Limpiar saltos muertos o bloques vacíos redundantes
    cleaned = re.sub(r"if\s*\(\s*false\s*\)\s*\{[^}]*\}", "// [dead code removed]", cleaned)
    cleaned = re.sub(r"if\s*\(\s*true\s*\)\s*\{([^}]*)\}", r"\1", cleaned)

    # 4. Simplificar dobles negaciones !!x -> x
    cleaned = re.sub(r"!!([a-zA-Z0-9_]+)", r"Boolean(\1)", cleaned)

    return cleaned
