"""Fix bare `return` -> `return parts` inside methods that collect ReplyPart."""

import ast
import re
from pathlib import Path

p = Path(r"c:\Users\Administrator\Documents\GitHub\NcatBot\plugins\Tataru\service.py")
t = p.read_text(encoding="utf-8")

# Within methods that have `parts: list[ReplyPart] = []`, change bare return to return parts
# excluding returns that already return something

lines = t.splitlines(keepends=True)
out = []
in_parts_method = False
for line in lines:
    if re.match(r"    async def \w+\(", line) or re.match(r"    def \w+\(", line):
        in_parts_method = False
    if "parts: list[ReplyPart] = []" in line:
        in_parts_method = True
    if in_parts_method and re.match(r"(\s+)return\s*$", line):
        indent = re.match(r"(\s+)return\s*$", line).group(1)
        out.append(f"{indent}return parts\n")
        continue
    out.append(line)

t2 = "".join(out)
ast.parse(t2)
p.write_text(t2, encoding="utf-8")
print("syntax OK, bare returns fixed")
