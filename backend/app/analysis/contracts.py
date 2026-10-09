"""Extract concrete HTTP contracts and fetch response uses without executing code.

The intentionally conservative rules require a literal FastAPI route and an
unambiguous named fetch/json binding. Syntax that cannot be proven stays unknown.
"""
import ast
from collections import Counter
import json
import re
from pathlib import PurePosixPath

from app.ingestion.syntax import node_text, parse_javascript, walk

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
FUNCTIONS = {"function_declaration", "function_expression", "arrow_function", "method_definition", "generator_function_declaration"}


def endpoint_key(value: str) -> str | None:
    """Normalize only repository-local URLs; external fetches are not dependencies."""
    if not value.startswith("/") or value.startswith("//"):
        return None
    value = value.split("?", 1)[0].split("#", 1)[0]
    return re.sub(r"\{[^/{}]+\}", "{*}", value).rstrip("/") or "/"


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _body_nodes(function):
    """Walk a function's body but exclude nested function and class bodies."""
    def descend(node):
        yield node
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            return
        for child in ast.iter_child_nodes(node):
            yield from descend(child)
    for statement in function.body:
        yield from descend(statement)


def _dict_fields(node, prefix=""):
    if not isinstance(node, ast.Dict) or any(key is None for key in node.keys):
        return None
    fields = {}
    for key, value in zip(node.keys, node.values):
        name = _literal(key)
        if name is None or "." in name:
            return None
        qualified = f"{prefix}.{name}" if prefix else name
        fields[qualified] = {"start_line": key.lineno, "end_line": value.end_lineno or key.lineno}
        if isinstance(value, ast.Dict):
            nested = _dict_fields(value, qualified)
            if nested is None:
                return None
            fields.update(nested)
    return fields


def _python_router_info(tree):
    constructors = set()
    fastapi_modules = set()
    imported_routers = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            if node.module == "fastapi":
                constructors.update(a.asname or a.name for a in node.names if a.name in {"FastAPI", "APIRouter"})
            else:
                for alias in node.names:
                    imported_routers[alias.asname or alias.name] = (node.module, alias.name)
        elif isinstance(node, ast.Import):
            fastapi_modules.update(alias.asname or alias.name for alias in node.names if alias.name == "fastapi")
    rebound = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            rebound.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            rebound.update(target.id for target in targets if isinstance(target, ast.Name))
    constructors -= rebound
    fastapi_modules -= rebound
    owners = {}
    mounts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            constructor = node.value.func
            known_constructor = isinstance(constructor, ast.Name) and constructor.id in constructors
            known_constructor |= isinstance(constructor, ast.Attribute) and isinstance(constructor.value, ast.Name) and constructor.value.id in fastapi_modules and constructor.attr in {"FastAPI", "APIRouter"}
            if known_constructor:
                prefix = next((_literal(k.value) for k in node.value.keywords if k.arg == "prefix"), "")
                if prefix is None:
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        owners[target.id] = prefix
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == "include_router" and call.args and isinstance(call.args[0], ast.Name):
                prefix = next((_literal(k.value) for k in call.keywords if k.arg == "prefix"), "")
                if prefix is not None:
                    mounts.setdefault(call.args[0].id, []).append(prefix)
    return owners, mounts, imported_routers


def extract_producers(files):
    parsed = {}
    for path, content in files.items():
        if PurePosixPath(path).suffix != ".py":
            continue
        try:
            parsed[path] = ast.parse(content)
        except SyntaxError:
            continue
    mounting = {}
    for path, tree in parsed.items():
        _, mounts, imports = _python_router_info(tree)
        for alias, prefixes in mounts.items():
            if alias in imports:
                module, imported_name = imports[alias]
                if module:
                    module_suffix = module.replace(".", "/") + ".py"
                    for imported_path in parsed:
                        if imported_path == module_suffix or imported_path.endswith("/" + module_suffix):
                            mounting.setdefault((imported_path, imported_name), []).extend(prefixes)
    producers = []
    for path, tree in parsed.items():
        owners, mounts, _ = _python_router_info(tree)
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            nodes = list(_body_nodes(function))
            assignments = {}
            mutated = set()
            for n in nodes:
                if isinstance(n, (ast.Assign, ast.AnnAssign)):
                    targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            assignments.setdefault(target.id, []).append(n.value)
                        elif isinstance(target, (ast.Subscript, ast.Attribute)) and isinstance(target.value, ast.Name):
                            mutated.add(target.value.id)
                elif isinstance(n, (ast.AugAssign, ast.Delete)):
                    targets = n.targets if isinstance(n, ast.Delete) else [n.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            mutated.add(target.id)
                        elif isinstance(target, (ast.Subscript, ast.Attribute)) and isinstance(target.value, ast.Name):
                            mutated.add(target.value.id)
                elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
                    mutated.add(n.func.value.id)
            return_fields = []
            for n in nodes:
                if not isinstance(n, ast.Return):
                    continue
                value = n.value
                if isinstance(value, ast.Name) and value.id not in mutated and len(assignments.get(value.id, [])) == 1:
                    value = assignments[value.id][0]
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "JSONResponse":
                    value = next((k.value for k in value.keywords if k.arg == "content"), value.args[0] if value.args else None)
                return_fields.append(_dict_fields(value))
            known_shape = bool(return_fields) and all(fields is not None for fields in return_fields)
            fields = {}
            if known_shape:
                for returned in return_fields:
                    fields.update(returned)
            for decorator in function.decorator_list:
                if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                    continue
                receiver, method = decorator.func.value, decorator.func.attr.lower()
                if not isinstance(receiver, ast.Name) or receiver.id not in owners or method not in HTTP_METHODS:
                    continue
                route = _literal(decorator.args[0]) if decorator.args else next((_literal(k.value) for k in decorator.keywords if k.arg == "path"), None)
                if route is None:
                    continue
                # A response model can add/filter fields and is not proven by a
                # literal return dictionary. Do not invent its effective schema.
                shape_known = known_shape and not any(k.arg == "response_model" and not (isinstance(k.value, ast.Constant) and k.value.value is None) for k in decorator.keywords)
                prefixes = mounting.get((path, receiver.id), mounts.get(receiver.id, [""]))
                for mount_prefix in prefixes:
                    endpoint = endpoint_key(mount_prefix + owners[receiver.id] + route)
                    if endpoint is None:
                        continue
                    producers.append({"path": path, "symbol": function.name, "endpoint": endpoint,
                                      "method": method.upper(), "fields": fields if shape_known else {}, "known_shape": shape_known,
                                      "guaranteed_fields": sorted(set.intersection(*(set(returned) for returned in return_fields))) if shape_known else [],
                                      "start_line": decorator.lineno, "end_line": function.end_lineno,
                                      "route_line": decorator.lineno})
    return producers


def _js_string(node, source, *, template=False):
    if node is None:
        return None
    text = node_text(node, source)
    if node.type == "string":
        if text.startswith('"'):
            try:
                return json.loads(text)
            except ValueError:
                return None
        if text.startswith("'") and "\\" not in text:
            return text[1:-1]
    if node.type == "template_string" and template:
        value = text[1:-1]
        # Each interpolation must occupy a complete path segment. Prefix/base
        # interpolations and embedded expressions cannot establish a local URL.
        normalized = re.sub(r"\$\{[^{}]+\}", "{*}", value)
        if "$" in normalized or not normalized.startswith("/"):
            return None
        if any("{*}" in part and part != "{*}" for part in normalized.split("/")):
            return None
        return normalized
    return None


def _unwrap(node):
    while node is not None and node.type in {"await_expression", "parenthesized_expression", "as_expression", "non_null_expression", "satisfies_expression"}:
        node = node.named_children[0] if node.named_children else None
    return node


def _awaited(node):
    while node is not None and node.type in {"parenthesized_expression", "as_expression", "non_null_expression", "satisfies_expression"}:
        node = node.named_children[0] if node.named_children else None
    return node is not None and node.type == "await_expression"


def _member(node, source):
    """Return root identifier and static property path, ignoring comments/types."""
    node = _unwrap(node)
    if node is None:
        return None
    if node.type == "identifier":
        return node_text(node, source), []
    if node.type in {"member_expression", "subscript_expression"}:
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property") or node.child_by_field_name("index")
        root = _member(obj, source)
        if not root or prop is None:
            return None
        if prop.type in {"property_identifier", "identifier"} and node.type == "member_expression":
            value = node_text(prop, source)
        else:
            value = _js_string(prop, source)
        return (root[0], root[1] + [value]) if value else None
    return None


def _guarded(node, source):
    """Known fallbacks do not establish an actionable broken field read."""
    current = node
    for _ in range(8):
        if current is None or current.type in {"return_statement", "expression_statement", "variable_declarator", "statement_block"}:
            break
        if current.type in {"member_expression", "call_expression", "subscript_expression"} and "?." in node_text(current, source):
            return True
        if current.type == "binary_expression":
            operator = current.child_by_field_name("operator")
            left = current.child_by_field_name("left")
            if operator is not None and node_text(operator, source) in {"??", "||"} and left is not None and left.start_byte <= node.start_byte < left.end_byte:
                return True
        current = current.parent
    return False


def _fetch(node, source, blocked=()):
    node = _unwrap(node)
    if node is None or node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    function_name = node_text(function, source) if function else ""
    if function_name not in {"fetch", "window.fetch", "globalThis.fetch"} or function_name.split(".")[0] in blocked:
        return None
    arguments = node.child_by_field_name("arguments")
    args = arguments.named_children if arguments else []
    if not args:
        return None
    url = _js_string(args[0], source, template=True)
    endpoint = endpoint_key(url) if url else None
    if not endpoint:
        return None
    method = "GET"
    if len(args) > 1:
        if args[1].type != "object":
            return None
        for pair in args[1].named_children:
            if pair.type == "spread_element":
                return None
            key = pair.child_by_field_name("key")
            key_text = node_text(key, source).strip("'\"") if key else ""
            if key_text == "method":
                method = _js_string(pair.child_by_field_name("value"), source)
                if method is None or method.upper() not in {m.upper() for m in HTTP_METHODS}:
                    return None
                method = method.upper()
    return {"endpoint": endpoint, "method": method, "fetch_line": node.start_point.row + 1}


def _json_result(node, source, responses, blocked=()):
    if not _awaited(node):
        return None
    node = _unwrap(node)
    if node is None or node.type != "call_expression":
        return None
    function = node.child_by_field_name("function")
    if function is None or function.type != "member_expression":
        return None
    prop = function.child_by_field_name("property")
    if prop is None or node_text(prop, source) != "json":
        return None
    raw_object = function.child_by_field_name("object")
    obj = _unwrap(raw_object)
    if obj is not None and obj.type == "identifier":
        return responses.get(node_text(obj, source))
    return _fetch(obj, source, blocked) if _awaited(raw_object) else None


def _scope_nodes(scope):
    """Keep lexical scopes isolated; closures require separate explicit analysis."""
    def descend(node):
        yield node
        for child in node.named_children:
            if child.type in FUNCTIONS and child != scope:
                continue
            yield from descend(child)
    return list(descend(scope))


def extract_consumers(files):
    consumers = []
    for path, content in files.items():
        first_file_consumer = len(consumers)
        if PurePosixPath(path).suffix not in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"}:
            continue
        root = parse_javascript(path, content)
        if root is None or root.has_error:
            continue
        source = content.encode()
        scopes = [root] + [node for node in walk(root) if node.type in FUNCTIONS]
        # If a global HTTP primitive is locally rebound anywhere in this source,
        # its provenance is uncertain. Conservatively omit those requests.
        blocked = set()
        for node in walk(root):
            if node.type in {"variable_declarator", "function_declaration", "class_declaration"}:
                binding = node.child_by_field_name("name")
                if binding is not None and node_text(binding, source) in {"fetch", "window", "globalThis"}:
                    blocked.add(node_text(binding, source))
            elif node.type in {"formal_parameters", "import_clause"}:
                for binding in walk(node):
                    if binding.type == "identifier" and node_text(binding, source) in {"fetch", "window", "globalThis"}:
                        blocked.add(node_text(binding, source))
            elif node.type == "assignment_expression":
                binding = node.child_by_field_name("left")
                if binding is not None and binding.type == "identifier" and node_text(binding, source) in {"fetch", "window", "globalThis"}:
                    blocked.add(node_text(binding, source))
                else:
                    member = _member(binding, source)
                    if member and member[0] in {"window", "globalThis"} and member[1] == ["fetch"]:
                        blocked.add(member[0])
                        blocked.add("fetch")
        for scope in scopes:
            first_consumer = len(consumers)
            nodes = _scope_nodes(scope)
            declarations = [node for node in nodes if node.type == "variable_declarator"]
            # A shadowed or reassigned binding has uncertain data flow; omit it.
            names = [node_text(n.child_by_field_name("name"), source) for n in declarations if n.child_by_field_name("name") is not None and n.child_by_field_name("name").type == "identifier"]
            name_counts = Counter(names)
            reassigned = set()
            overwritten_response = set()
            for node in nodes:
                if node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"} or (node.type == "unary_expression" and node_text(node, source).lstrip().startswith("delete ")):
                    target = node.child_by_field_name("left") or node.child_by_field_name("argument")
                    if target is not None and target.type == "identifier":
                        reassigned.add(node_text(target, source))
                    else:
                        member = _member(target, source)
                        if member and member[1] and member[1][0] == "json":
                            overwritten_response.add(member[0])
            unsafe = {name for name, count in name_counts.items() if count != 1} | reassigned
            responses, payloads = {}, {}
            for declaration in sorted(declarations, key=lambda n: n.start_byte):
                target = declaration.child_by_field_name("name")
                value = declaration.child_by_field_name("value")
                if target is None or value is None:
                    continue
                if target.type == "identifier":
                    identifier = node_text(target, source)
                    if identifier in unsafe:
                        continue
                    fetched = _fetch(value, source, blocked)
                    if fetched and _awaited(value) and identifier not in overwritten_response:
                        responses[identifier] = fetched
                    payload = _json_result(value, source, responses, blocked)
                    if payload:
                        payloads[identifier] = {**payload, "prefix": []}
                    else:
                        alias = _member(value, source)
                        if alias and alias[0] in payloads:
                            payloads[identifier] = {**payloads[alias[0]], "prefix": payloads[alias[0]]["prefix"] + alias[1]}
                elif target.type == "object_pattern":
                    payload = _json_result(value, source, responses, blocked)
                    member = _member(value, source)
                    if payload is None and member and member[0] in payloads:
                        payload = {**payloads[member[0]], "prefix": payloads[member[0]]["prefix"] + member[1]}
                    if payload:
                        fields = {}
                        for part in target.named_children:
                            if part.type == "shorthand_property_identifier_pattern":
                                field = node_text(part, source)
                            elif part.type == "pair_pattern":
                                key = part.child_by_field_name("key")
                                field = node_text(key, source).strip("'\"") if key else None
                            else:
                                field = None
                            if field:
                                full_field = ".".join(payload.get("prefix", []) + [field])
                                fields[full_field] = {"start_line": part.start_point.row + 1, "end_line": part.end_point.row + 1}
                        if fields:
                            consumers.append({**payload, "path": path, "fields": fields, "start_line": declaration.start_point.row + 1, "end_line": declaration.end_point.row + 1})
            overwritten_by_request = {}
            for node in nodes:
                if node.type in {"assignment_expression", "augmented_assignment_expression", "update_expression"} or (node.type == "unary_expression" and node_text(node, source).lstrip().startswith("delete ")):
                    target = node.child_by_field_name("left") or node.child_by_field_name("argument")
                    member = _member(target, source)
                    if member and member[0] in payloads and member[1]:
                        payload = payloads[member[0]]
                        overwritten_by_request.setdefault(payload["fetch_line"], set()).add(".".join(payload["prefix"] + member[1]))
                elif node.type == "call_expression":
                    function = node.child_by_field_name("function")
                    arguments = node.child_by_field_name("arguments")
                    if function is not None and node_text(function, source) == "Object.assign" and arguments and arguments.named_children:
                        target = arguments.named_children[0]
                        if target.type == "identifier" and node_text(target, source) in payloads:
                            overwritten_by_request.setdefault(payloads[node_text(target, source)]["fetch_line"], set()).add("")
                    elif arguments and function is not None and node_text(function, source) not in {"JSON.stringify", "Object.keys", "Object.values", "Object.entries", "Object.freeze", "Array.isArray"}:
                        for argument in arguments.named_children:
                            if argument.type == "identifier" and node_text(argument, source) in payloads:
                                # An opaque function may mutate a payload. Its
                                # later fields cannot be attributed to the server.
                                overwritten_by_request.setdefault(payloads[node_text(argument, source)]["fetch_line"], set()).add("")
            for identifier, payload in payloads.items():
                fields = {}
                overwritten = overwritten_by_request.get(payload["fetch_line"], set())
                for node in nodes:
                    if node.type not in {"member_expression", "subscript_expression"}:
                        continue
                    member = _member(node, source)
                    if member and member[0] == identifier and member[1]:
                        field = ".".join(payload["prefix"] + member[1])
                        if any(not prefix or field == prefix or field.startswith(prefix + ".") for prefix in overwritten):
                            continue
                        guarded = _guarded(node, source)
                        previous = fields.get(field)
                        fields[field] = {"start_line": node.start_point.row + 1, "end_line": node.end_point.row + 1,
                                         "guarded": guarded and (previous is None or previous.get("guarded", False))}
                consumers.append({**payload, "path": path, "response_identifier": identifier, "fields": fields,
                                  "start_line": payload["fetch_line"], "end_line": max([payload["fetch_line"]] + [f["end_line"] for f in fields.values()])})
            for consumer in consumers[first_consumer:]:
                overwritten = overwritten_by_request.get(consumer["fetch_line"], set())
                consumer["fields"] = {field: location for field, location in consumer["fields"].items()
                                      if not any(not prefix or field == prefix or field.startswith(prefix + ".") for prefix in overwritten)}
            # Even without json(), an unchanged fetch proves an endpoint dependency.
            matched_requests = {c["fetch_line"] for c in consumers[first_file_consumer:]}
            for node in nodes:
                fetched = _fetch(node, source, blocked)
                if fetched and fetched["fetch_line"] not in matched_requests:
                    consumers.append({**fetched, "path": path, "fields": {}, "start_line": node.start_point.row + 1, "end_line": node.end_point.row + 1})
                    matched_requests.add(fetched["fetch_line"])
    # Multiple scopes can see a function declaration node, so deduplicate explicitly.
    unique = {}
    for consumer in consumers:
        key = (consumer["path"], consumer["endpoint"], consumer["method"], consumer["fetch_line"], tuple(sorted(consumer["fields"])))
        unique[key] = consumer
    return list(unique.values())
