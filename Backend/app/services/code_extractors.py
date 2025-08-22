import os
import httpx
import json
import tempfile
import time
import os
import re
import zipfile
import shutil

def _dedupe(seq):
    return list(dict.fromkeys(seq))

def _safe_json_loads(s: str, fallback: any = None) -> any:
    """
    Best-effort JSON loader:
    - tries whole string
    - falls back to extracting the first top-level JSON object with a regex
    """
    try:
        return json.loads(s)
    except Exception:
        pass
    # Extract the first {...} block (naive but robust enough for LLMs)
    try:
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(s[start:end+1])
    except Exception:
        pass
    return fallback

def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def extract_python_symbols(path: str) -> dict:
    import ast
    src = _read_text(path)
    out = {"functions": [], "classes": [], "imports": [], "routes": []}
    if not src:
        return out
    try:
        tree = ast.parse(src)
    except Exception:
        return out

    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            out["functions"].append(node.name)
            # Flask route decorator detection
            for dec in node.decorator_list:
                s = ast.unparse(dec) if hasattr(ast, "unparse") else ""
                if "app.route(" in s or "bp.route(" in s:
                    out["routes"].append(node.name)
        elif isinstance(node, ast.ClassDef):
            out["classes"].append(node.name)
        elif isinstance(node, ast.Import):
            for n in node.names:
                out["imports"].append(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                out["imports"].append(node.module.split(".")[0])

    # Quick regex catch for common Flask patterns
    if "from flask" in src or "import flask" in src:
        for m in re.finditer(r"@(?:app|bp)\.route\(['\"][^'\"]+['\"]", src):
            out["routes"].append(m.group(0))
    # SQL usage hints
    if re.search(r"\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b", src, re.IGNORECASE):
        out.setdefault("signals", []).append("uses_sql_queries")
    return {k: _dedupe(v) for k, v in out.items()}

def extract_js_ts_symbols(path: str) -> dict:
    src = _read_text(path)
    out = {"functions": [], "classes": [], "imports": [], "routes": []}
    if not src:
        return out

    # imports (ESM / CJS)
    out["imports"].extend([m.group(1) for m in re.finditer(r"from\s+['\"]([^'\"]+)['\"]", src)])
    out["imports"].extend([m.group(1) for m in re.finditer(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", src)])

    # functions
    out["functions"].extend([m.group(1) for m in re.finditer(r"function\s+([A-Za-z0-9_]+)\s*\(", src)])
    out["functions"].extend([m.group(1) for m in re.finditer(r"const\s+([A-Za-z0-9_]+)\s*=\s*\(", src)])
    out["functions"].extend([m.group(1) for m in re.finditer(r"([A-Za-z0-9_]+)\s*=\s*\([\w\s,]*\)\s*=>", src)])

    # classes
    out["classes"].extend([m.group(1) for m in re.finditer(r"class\s+([A-Za-z0-9_]+)\s*", src)])

    # Express routes
    for m in re.finditer(r"\b(app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"][^'\"]+['\"]", src):
        out["routes"].append(m.group(0))

    # SQL hints
    if re.search(r"\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b", src, re.IGNORECASE):
        out.setdefault("signals", []).append("uses_sql_queries")

    return {k: _dedupe(v) for k, v in out.items()}

def summarize_repo_code(root: str) -> dict:
    """
    Walks repo directory, extracts code-level signals for Python & JS/TS.
    Extendable for Java/Go/C++ later.
    """
    code = {
        "python": {"files": 0, "functions": [], "classes": [], "imports": [], "routes": [], "signals": []},
        "js_ts": {"files": 0, "functions": [], "classes": [], "imports": [], "routes": [], "signals": []},
    }
    for dirpath, dirnames, filenames in os.walk(root):
        # Skip heavy dirs
        if any(seg in dirpath for seg in (".git", "node_modules", "dist", "build", ".venv", "venv", ".mypy_cache", ".pytest_cache")):
            continue
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            if fn.endswith(".py"):
                code["python"]["files"] += 1
                sym = extract_python_symbols(fp)
                for k in ("functions", "classes", "imports", "routes"):
                    code["python"][k].extend(sym.get(k, []))
                code["python"]["signals"].extend(sym.get("signals", []))
            elif fn.endswith((".js", ".ts", ".jsx", ".tsx")):
                code["js_ts"]["files"] += 1
                sym = extract_js_ts_symbols(fp)
                for k in ("functions", "classes", "imports", "routes"):
                    code["js_ts"][k].extend(sym.get(k, []))
                code["js_ts"]["signals"].extend(sym.get("signals", []))

    # dedupe
    for lang in code:
        for k in code[lang]:
            if isinstance(code[lang][k], list):
                code[lang][k] = _dedupe(code[lang][k])
    return code
