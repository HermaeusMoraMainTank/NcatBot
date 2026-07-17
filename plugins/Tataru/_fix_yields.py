import ast
import re
from pathlib import Path

p = Path(r"c:\Users\Administrator\Documents\GitHub\NcatBot\plugins\Tataru\service.py")
t = p.read_text(encoding="utf-8")


def fix_plain(m):
    inner = m.group(1).strip()
    return f"parts.append(ReplyPart.text({inner}))"


def fix_chain(m):
    inner = m.group(1).strip()
    return f"parts.extend({inner})"


t2 = re.sub(
    r"yield event\.plain_result\(\s*(.*?)\s*\)",
    fix_plain,
    t,
    flags=re.S,
)
t2 = re.sub(
    r"yield event\.chain_result\(\s*(.*?)\s*\)",
    fix_chain,
    t2,
    flags=re.S,
)

try:
    ast.parse(t2)
    print("OK after fix")
except SyntaxError as e:
    print("ERR", e)
    lines = t2.splitlines()
    for n in range(max(1, e.lineno - 2), min(len(lines), e.lineno + 3) + 1):
        print(n, lines[n - 1])

print("yield left", t2.count("yield "))
for n, L in enumerate(t2.splitlines(), 1):
    if "yield " in L:
        print(n, L[:120])

p.write_text(t2, encoding="utf-8")
print("written")
