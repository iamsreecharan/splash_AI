"""Ground truth across response bindings, changed endpoints and safe changes.

These synthetic cases test the supported static rules, not arbitrary runtime
behavior. Each variant has independent source and expected impacted fields.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Case:
    name: str
    category: str
    base: dict[str, str]
    head: dict[str, str]
    expected_alerts: int
    expected_fields: tuple[str, ...] = ()
    producer_path: str = "api/service.py"
    consumer_path: str = "web/consumer.ts"


def producer(field="total", endpoint="/api/items", *, method="get", extra=""):
    return f'''from fastapi import FastAPI
app = FastAPI()
@app.{method}("{endpoint}")
async def read_items():
    return {{"{field}": 12, "id": "record-1"{extra}}}
'''


def consumer(field="total", endpoint="/api/items", mode="dot"):
    if mode == "direct":
        body = f'const data = await (await fetch("{endpoint}")).json();\n  return data.{field};'
    elif mode == "destructure":
        body = f'const response = await fetch("{endpoint}");\n  const {{{field}: value}} = await response.json();\n  return value;'
    elif mode == "bracket":
        body = f'const response = await fetch("{endpoint}");\n  const data = await response.json();\n  return data["{field}"];'
    else:
        body = f'const response = await fetch("{endpoint}");\n  const data = await response.json();\n  return data.{field};'
    return f'export async function load() {{\n  {body}\n}}\n'


def build_cases():
    result = []
    for field in ("total", "temperature", "status", "email", "quantity", "latency", "priority", "balance"):
        endpoint = f"/api/{field}/current"
        before = producer(field, endpoint)
        after = producer(f"current_{field}", endpoint)
        for mode in ("dot", "direct", "destructure", "bracket"):
            client = consumer(field, endpoint, mode)
            result.append(Case(f"rename-{field}-{mode}", "field-removal", {"api/service.py": before, "web/consumer.ts": client},
                               {"api/service.py": after, "web/consumer.ts": client}, 1, (field,)))
        base = {"api/service.py": before, "web/consumer.ts": consumer(field, endpoint)}
        result.extend([
            Case(f"safe-addition-{field}", "safe-addition", base,
                 {**base, "api/service.py": producer(field, endpoint, extra=', "new_optional_key": true'.replace("true", "True"))}, 0),
            Case(f"updated-consumer-{field}", "coordinated-migration", base,
                 {"api/service.py": after, "web/consumer.ts": consumer(f"current_{field}", endpoint)}, 0),
            Case(f"other-endpoint-{field}", "unrelated-endpoint",
                 {"api/service.py": before, "web/consumer.ts": consumer(field, "/api/independent")},
                 {"api/service.py": after, "web/consumer.ts": consumer(field, "/api/independent")}, 0),
            Case(f"local-token-{field}", "unrelated-symbol",
                 {"api/service.py": before, "web/consumer.ts": f'const state = {{"{field}": 10}}; console.log(state.{field});'},
                 {"api/service.py": after, "web/consumer.ts": f'const state = {{"{field}": 10}}; console.log(state.{field});'}, 0),
        ])
    base = {"api/service.py": producer(), "web/consumer.ts": consumer()}
    result.extend([
        Case("endpoint-rename", "endpoint-change", base, {**base, "api/service.py": producer(endpoint="/api/orders")}, 1),
        Case("endpoint-removal", "endpoint-change", base, {**base, "api/service.py": 'from fastapi import FastAPI\napp = FastAPI()\n'}, 1),
        Case("coordinated-endpoint-rename", "coordinated-migration", base,
             {"api/service.py": producer(endpoint="/api/orders"), "web/consumer.ts": consumer(endpoint="/api/orders")}, 0),
        Case("external-endpoint", "unrelated-endpoint",
             {"api/service.py": producer(), "web/consumer.ts": consumer(endpoint="https://external.example/api/items")},
             {"api/service.py": producer("new_total"), "web/consumer.ts": consumer(endpoint="https://external.example/api/items")}, 0),
        Case("syntax-error-unknown", "ambiguous-producer", base, {**base, "api/service.py": producer() + '\ndef invalid(\n'}, 0),
        Case("dynamic-route-unknown", "ambiguous-producer", base,
             {**base, "api/service.py": producer().replace('@app.get("/api/items")', 'ROUTE = "/api/items"\n@app.get(ROUTE)')}, 0),
        Case("generated-head-unknown", "excluded-producer", base, {**base, "api/service.py": "# @generated\n" + producer()}, 0),
        Case("reassigned-payload", "ambiguous-consumer",
             {"api/service.py": producer(), "web/consumer.ts": consumer().replace('return data.total;', 'data = {total: 4}; return data.total;')},
             {"api/service.py": producer("new_total"), "web/consumer.ts": consumer().replace('return data.total;', 'data = {total: 4}; return data.total;')}, 0),
        Case("locally-overwritten-field", "ambiguous-consumer",
             {"api/service.py": producer(), "web/consumer.ts": consumer().replace('return data.total;', 'data.total = 4; return data.total;')},
             {"api/service.py": producer("new_total"), "web/consumer.ts": consumer().replace('return data.total;', 'data.total = 4; return data.total;')}, 0),
        Case("shadowed-fetch", "ambiguous-consumer",
             {"api/service.py": producer(), "web/consumer.ts": consumer().replace('load()', 'load(fetch: any)')},
             {"api/service.py": producer("new_total"), "web/consumer.ts": consumer().replace('load()', 'load(fetch: any)')}, 0),
    ])
    return result


CASES = build_cases()
