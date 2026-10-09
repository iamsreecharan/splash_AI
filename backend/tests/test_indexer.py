from app.ingestion.indexer import index_files
from app.ingestion.paths import MAX_FILE_BYTES, allowed_path
from app.analysis.fixtures import BASE_FILES


def test_structural_chunks_bind_lines_symbols_and_revision():
    files = {"api/demo.py": 'import os\n\ndef first():\n    return 1\n\nclass Machine:\n    def read(self):\n        return 2\n'}
    result = index_files(files, "demo/repo", "a" * 40)
    assert any(c["symbol"] == "first" and c["kind"] == "function" for c in result["chunks"])
    assert any(c["symbol"] == "Machine.read" for c in result["chunks"])
    for chunk in result["chunks"]:
        assert chunk["content"] == "\n".join(files[chunk["path"]].splitlines()[chunk["start_line"] - 1:chunk["end_line"]])
        assert chunk["commit_sha"] == "a" * 40


def test_js_typescript_and_docs_have_useful_chunks():
    files = {"web/read.ts": 'export async function read(): Promise<number> { return 1; }',
             "README.md": '# Architecture\nThe dashboard consumes the machine endpoint.\n',
             "package.json": '{"scripts": {"dev": "vite"}}'}
    result = index_files(files)
    assert result["indexed_files"] == 3
    assert any(c["symbol"] == "read" and c["kind"] == "function" for c in result["chunks"])
    assert {c["kind"] for c in result["chunks"]} >= {"function", "documentation", "manifest"}


def test_indexer_omits_unsafe_and_irrelevant_input():
    files = {".env": "TOKEN=secret", "../escape.py": "print(1)", "dist/file.js": "function x(){}",
             "node_modules/lib/index.js": "function x(){}", "api/generated.py": "# @generated\nx=1",
             "api/binary.py": "\x00", "api/large.py": "#" * (MAX_FILE_BYTES + 1),
             "api/credentials.py": "x=1", "api/private.py": 'api_key = "sk-' + "x" * 30 + '"',
             "api/okay.py": "def okay():\n    return 1\n"}
    result = index_files(files)
    assert result["indexed_files"] == 1
    assert {chunk["path"] for chunk in result["chunks"]} == {"api/okay.py"}


def test_only_concrete_http_dependencies_are_indexed():
    result = index_files(BASE_FILES)
    assert len(result["dependencies"]) == 1
    assert result["dependencies"][0]["verified"]
    unrelated = {"web/random.ts": 'const temperature = 10; console.log(temperature);'}
    assert index_files(unrelated)["dependencies"] == []


def test_safe_path_validation():
    for path in ("/absolute.py", "a/../b.py", "a\\b.py", "secret.json", "secrets.py", "id_rsa", ".env.py"):
        assert not allowed_path(path)
    assert allowed_path("api/machines.py")
    assert allowed_path("README.md")
