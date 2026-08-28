import sys, re, os
base = sys.argv[1]; no = sys.argv[2]
txt = open(os.path.join(base, no+'.txt'), encoding='utf-8').read()
parts = re.split(r'\n########## (.+?) ##########\n', txt)
# parts: [pre, name1, body1, name2, body2...]
KEY = re.compile(r'규격|사양|명세|내역|제안요청|평가기준')
BAD = re.compile(r'유의서|서약|각서|확약|검토확인서|입찰서|양식|평가기준|제안요청')
out=[]
for i in range(1, len(parts), 2):
    name, body = parts[i], parts[i+1]
    if KEY.search(name) and not BAD.search(name):
        body = re.sub(r"\.[A-Za-z][\w-]*\s*\{[^}]*\}", "", body); body = re.sub(r"[ \t]{2,}", " ", body)
        body = re.sub(r'\n{2,}', '\n', body)
        out.append(f"===== {name} =====\n{body.strip()}")
print(("\n\n".join(out))[:int(sys.argv[3]) if len(sys.argv)>3 else 12000])
