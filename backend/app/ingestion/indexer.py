"""Symbol-level chunks for source search. No imports or repository code execution."""
import ast
from pathlib import PurePosixPath
from app.ingestion.paths import source_files
from app.ingestion.syntax import node_text, parse_javascript, walk

MAX_CHUNK_LINES = 160


def _chunk(path, lines, start, end, symbol, kind):
    return {"path": path, "start_line": start, "end_line": end,
            "content": "\n".join(lines[start - 1:end]), "symbol": symbol, "kind": kind}


def _split(path, lines, start, end, symbol, kind):
    for first in range(start, end + 1, MAX_CHUNK_LINES):
        yield _chunk(path, lines, first, min(first + MAX_CHUNK_LINES - 1, end), symbol, kind)


def _python_chunks(path, content):
    lines = content.splitlines()
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return list(_split(path, lines, 1, len(lines), "", "unparsed"))
    chunks = []
    cursor = 1
    for node in tree.body:
        start = min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        end = node.end_lineno or start
        if cursor < start:
            chunks.extend(_split(path, lines, cursor, start - 1, "", "module"))
        symbol = getattr(node, "name", "")
        kind = "class" if isinstance(node, ast.ClassDef) else "function" if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "module"
        if isinstance(node, ast.ClassDef):
            methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if methods:
                chunks.extend(_split(path, lines, start, methods[0].lineno - 1, symbol, "class"))
                for method in methods:
                    first = min([method.lineno] + [d.lineno for d in method.decorator_list])
                    chunks.extend(_split(path, lines, first, method.end_lineno, f"{symbol}.{method.name}", "method"))
            else:
                chunks.extend(_split(path, lines, start, end, symbol, kind))
        else:
            chunks.extend(_split(path, lines, start, end, symbol, kind))
        cursor = end + 1
    if cursor <= len(lines):
        chunks.extend(_split(path, lines, cursor, len(lines), "", "module"))
    return chunks


def _js_chunks(path, content):
    lines = content.splitlines()
    root = parse_javascript(path, content)
    if root is None or root.has_error:
        return list(_split(path, lines, 1, len(lines), "", "unparsed"))
    source = content.encode()
    chunks = []
    cursor = 1
    for node in root.named_children:
        start, end = node.start_point.row + 1, node.end_point.row + 1
        if cursor < start:
            chunks.extend(_split(path, lines, cursor, start - 1, "", "module"))
        declaration = next((n for n in walk(node) if n.type in {"function_declaration", "class_declaration", "variable_declarator", "method_definition"}), None)
        name = declaration.child_by_field_name("name") if declaration else None
        symbol = node_text(name, source) if name else ""
        kind = "function" if any(n.type in {"function_declaration", "arrow_function", "function_expression"} for n in walk(node)) else "module"
        chunks.extend(_split(path, lines, start, end, symbol, kind))
        cursor = end + 1
    if cursor <= len(lines):
        chunks.extend(_split(path, lines, cursor, len(lines), "", "module"))
    return chunks


def index_files(files, repository_name="", commit_sha=""):
    accepted = source_files(files)
    chunks = []
    for path, content in accepted.items():
        suffix = PurePosixPath(path).suffix
        if suffix == ".py":
            extracted = _python_chunks(path, content)
        elif suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            extracted = _js_chunks(path, content)
        else:
            extracted = list(_split(path, content.splitlines(), 1, len(content.splitlines()), "", "documentation" if suffix in {".md", ".mdx", ".rst"} else "manifest"))
        for chunk in extracted:
            chunk.update(repository_name=repository_name, commit_sha=commit_sha)
            chunks.append(chunk)
    # Import locally so basic chunking does not depend on the analysis graph.
    from app.analysis.contracts import extract_producers, extract_consumers
    producers = extract_producers(accepted)
    consumers = extract_consumers(accepted)
    dependencies = [{"kind": "http_response", "producer_path": p["path"],
                     "producer_symbol": p["symbol"], "consumer_path": c["path"],
                     "endpoint": p["endpoint"], "method": p["method"],
                     "fields": c["fields"], "verified": True}
                    for p in producers for c in consumers
                    if p["endpoint"] == c["endpoint"] and p["method"] == c["method"]]
    return {"chunks": chunks, "dependencies": dependencies,
            "indexed_files": len(accepted), "skipped_files": len(files) - len(accepted)}
