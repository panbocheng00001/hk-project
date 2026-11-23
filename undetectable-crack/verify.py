import hashlib
a=r"C:\Users\Administrator\.dsh\attachments\v1\files\aa\aa0797b3cf8d2ab99d58f51a29f267d10a67d503d1095510e766b6cb17a9946a\Undetectable.exe"
b=r"D:\tool-project\ads\undetectable-crack\out\Undetectable.exe"
da=open(a,"rb").read(); db=open(b,"rb").read()
print("len", len(da), len(db))
d=[i for i in range(len(da)) if da[i]!=db[i]]
print("diff count", len(d), [hex(x) for x in d])
for name,va in (("P1",0x140213400),("P2",0x1401FFC04),("P3",0x14023B76A)):
    o=va-0x140001000+0x400
    print(name, hex(o), db[o:o+8].hex())
print("sha256", hashlib.sha256(db).hexdigest())
