import os
import httpx
import json
import tempfile
import time
import os
import re
import zipfile
import shutil

from typing import Dict,List,Any,Optional

def _dedupe(seq):
    return list(dict.fromkeys(seq))

def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:
        return ""

def extract_python_symbols(path: str) -> Dict:
    import ast
    src = _read_text(path)
    out = {"functions": [], "classes": [], "imports": [], "routes": [], "signals": []}
    if not src: return out
    try:
        tree = ast.parse(src)
    except Exception:
        return out
    for node in ast.walk(tree):
        if hasattr(__import__("ast"), "FunctionDef") and isinstance(node, ast.FunctionDef):
            out["functions"].append(node.name)
            for dec in node.decorator_list:
                s = ast.unparse(dec) if hasattr(ast, "unparse") else ""
                if "app.route(" in s or "bp.route(" in s:
                    out["routes"].append(node.name)
        elif isinstance(node, ast.ClassDef):
            out["classes"].append(node.name)
        elif isinstance(node, (ast.Import,)):
            for n in node.names: out["imports"].append(n.name.split(".")[0])
        elif isinstance(node, (ast.ImportFrom,)):
            if node.module: out["imports"].append(node.module.split(".")[0])
    if "from flask" in src or "import flask" in src:
        for m in re.finditer(r"@(?:app|bp)\.route\(['\"][^'\"]+['\"]", src):
            out["routes"].append(m.group(0))
    if re.search(r"\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b", src, re.IGNORECASE):
        out["signals"].append("uses_sql_queries")
    return {k: _dedupe(v) for k, v in out.items()}

def extract_js_ts_symbols(path: str) -> Dict:
    src = _read_text(path)
    out = {"functions": [], "classes": [], "imports": [], "routes": [], "signals": []}
    if not src: return out
    out["imports"].extend([m.group(1) for m in re.finditer(r"from\s+['\"]([^'\"]+)['\"]", src)])
    out["imports"].extend([m.group(1) for m in re.finditer(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)", src)])
    out["functions"].extend([m.group(1) for m in re.finditer(r"function\s+([A-Za-z0-9_]+)\s*\(", src)])
    out["functions"].extend([m.group(1) for m in re.finditer(r"const\s+([A-Za-z0-9_]+)\s*=\s*\(", src)])
    out["functions"].extend([m.group(1) for m in re.finditer(r"([A-Za-z0-9_]+)\s*=\s*\([\w\s,]*\)\s*=>", src)])
    out["classes"].extend([m.group(1) for m in re.finditer(r"class\s+([A-Za-z0-9_]+)\s*", src)])
    for m in re.finditer(r"\b(app|router)\.(get|post|put|delete|patch)\s*\(\s*['\"][^'\"]+['\"]", src):
        out["routes"].append(m.group(0))
    if re.search(r"\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b", src, re.IGNORECASE):
        out["signals"].append("uses_sql_queries")
    return {k: _dedupe(v) for k, v in out.items()}

def summarize_repo_code(root: str) -> Dict:
    code = {
        "python": {"files": 0, "functions": [], "classes": [], "imports": [], "routes": [], "signals": []},
        "js_ts": {"files": 0, "functions": [], "classes": [], "imports": [], "routes": [], "signals": []},
    }
    for dirpath, dirnames, filenames in os.walk(root):
        
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
    
    for lang in code:
        for k, v in code[lang].items():
            if isinstance(v, list):
                code[lang][k] = _dedupe(v)
    
    return code