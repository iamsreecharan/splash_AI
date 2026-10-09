"""Tree-sitter helpers shared by indexing and JavaScript dependency extraction."""
from functools import lru_cache
from pathlib import PurePosixPath


@lru_cache(maxsize=3)
def parser_for(extension: str):
    try:
        from tree_sitter import Language, Parser
        if extension == ".tsx":
            import tree_sitter_typescript
            language = Language(tree_sitter_typescript.language_tsx())
        elif extension == ".ts":
            import tree_sitter_typescript
            language = Language(tree_sitter_typescript.language_typescript())
        else:
            import tree_sitter_javascript
            language = Language(tree_sitter_javascript.language())
        return Parser(language)
    except ImportError:
        return None


def parse_javascript(path: str, content: str):
    parser = parser_for(PurePosixPath(path).suffix.lower())
    if parser is None:
        return None
    return parser.parse(content.encode("utf-8")).root_node


def walk(node):
    yield node
    for child in node.named_children:
        yield from walk(child)


def node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")
