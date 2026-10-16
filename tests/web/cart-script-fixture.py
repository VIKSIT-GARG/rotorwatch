"""Extract the actual fixed browser scripts without importing application dependencies."""

import ast
import json
from pathlib import Path
from string import Template

tree = ast.parse(Path("src/drone_assistant/cart.py").read_text())
inspect = next(
    ast.literal_eval(n.value)
    for n in tree.body
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "INSPECT" for t in n.targets)
)
template = next(
    ast.literal_eval(n.args[0])
    for n in ast.walk(tree)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Template"
)
submit = Template(template).substitute(
    INSPECT=inspect,
    URL=json.dumps("https://robu.in/product/pi5/"),
    VARIANT='""',
    PRICE=json.dumps("₹6,000"),
    QTY=2,
)
print(json.dumps({"inspect": inspect, "submit": submit}))
