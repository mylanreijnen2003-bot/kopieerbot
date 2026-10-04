#!/usr/bin/env python3
"""Kluis: bestanden met volledige wallet-adressen versleuteld bewaren (AVG; de repo is openbaar).

Gebruik (sleutel in env KLUIS_KEY, gevuld uit secret DATA_KEY):
  python3 bt/kluis.py open <map>    alle *.kluis in <map> ontsleutelen naast het origineel
  python3 bt/kluis.py dicht <map>   nieuwe/gewijzigde bestanden (git) met volledige adressen
                                    -> <naam>.kluis, origineel weg; .md/.log/.txt krijgen
                                    afgekorte adressen (0x2555…34b1)
  python3 bt/kluis.py alles <map>   als dicht, maar voor alle bestanden (eenmalig opschonen)
  python3 bt/kluis.py show <pad>    bestand van origin/results naar stdout (ontsleutelt <pad>.kluis)
  python3 bt/kluis.py check <map>   meldt bestanden die nog leesbare volledige adressen bevatten

Zonder sleutel stopt dicht met een fout, zodat er nooit iets leesbaar wordt opgeslagen.
Versleuteling is deterministisch (zout = HMAC van de inhoud): ongewijzigde inhoud geeft
dezelfde bytes, dus geen onnodige wijzigingen in git. Bestanden *.enc (Lighter-chat, eigen
sleutel) worden niet aangeraakt.
"""
import gzip, hashlib, hmac, io, os, re, subprocess, sys, zipfile

ADR = re.compile(rb"0x[0-9a-fA-F]{40}")
TEKST = (".md", ".log", ".txt")
OVERSLAAN = (".kluis", ".enc")
ITER = "10000"
CIPHER = ["-aes-256-cbc", "-pbkdf2", "-iter", ITER, "-pass", "env:KLUIS_KEY"]


def kort(b):
    return ADR.sub(lambda m: m.group()[:6] + "…".encode() + m.group()[-4:], b)


def sleutel():
    k = os.environ.get("KLUIS_KEY", "")
    if not k:
        sys.exit("kluis: KLUIS_KEY ontbreekt (secret DATA_KEY); gestopt zodat er niets leesbaar wordt opgeslagen")
    return k


def gevoelig(pad, data):
    if ADR.search(data):
        return True
    if pad.endswith(".gz"):
        try:
            return bool(ADR.search(gzip.decompress(data)))
        except Exception:
            return True
    if pad.endswith(".zip"):
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            return any(ADR.search(z.read(n)) for n in z.namelist())
        except Exception:
            return True
    if pad.endswith(".parquet"):
        return any(w in data for w in (b"address", b"user", b"wallet", b"trader"))
    return False


def versleutel(data, k):
    zout = hmac.new(k.encode(), data, hashlib.sha256).digest()[:8]
    r = subprocess.run(["openssl", "enc"] + CIPHER + ["-S", zout.hex()],
                       input=data, capture_output=True, check=True)
    return b"Salted__" + zout + r.stdout


def ontsleutel(data):
    r = subprocess.run(["openssl", "enc", "-d"] + CIPHER, input=data, capture_output=True)
    if r.returncode:
        sys.exit("kluis: ontsleutelen mislukt (verkeerde DATA_KEY?)")
    return r.stdout


def lees(p):
    with open(p, "rb") as f:
        return f.read()


def schrijf(p, b):
    with open(p, "wb") as f:
        f.write(b)


def verwerk(p, k):
    """Eén bestand: niets, afkorten of versleutelen. Geeft actie terug."""
    if p.endswith(OVERSLAAN) or not os.path.isfile(p):
        return None
    data = lees(p)
    kl = p + ".kluis"
    if not gevoelig(p, data):
        if os.path.exists(kl):
            os.remove(kl)  # verouderde versleutelde versie
        return None
    if p.endswith(TEKST):
        schrijf(p, kort(data))
        if os.path.exists(kl):
            os.remove(kl)
        return "afgekort"
    enc = versleutel(data, k)
    if not (os.path.exists(kl) and lees(kl) == enc):
        schrijf(kl, enc)
    os.remove(p)
    return "versleuteld"


def alle_bestanden(m):
    for root, dirs, files in os.walk(m):
        dirs[:] = [d for d in dirs if d != ".git"]
        for f in files:
            if f != ".git":
                yield os.path.join(root, f)


def gewijzigd(m):
    r = subprocess.run(["git", "ls-files", "-m", "-o", "--exclude-standard", "-z"],
                       cwd=m, capture_output=True, check=True)
    for p in sorted(set(x for x in r.stdout.decode().split("\0") if x)):
        yield os.path.join(m, p)


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, arg = sys.argv[1], sys.argv[2]
    if cmd == "open":
        n = 0
        for p in alle_bestanden(arg):
            if p.endswith(".kluis"):
                if n == 0:
                    sleutel()
                schrijf(p[:-6], ontsleutel(lees(p)))
                n += 1
        print(f"kluis: {n} bestanden geopend")
    elif cmd in ("dicht", "alles"):
        k = sleutel()
        bron = alle_bestanden(arg) if cmd == "alles" else gewijzigd(arg)
        tel = {}
        for p in list(bron):
            a = verwerk(p, k)
            if a:
                tel[a] = tel.get(a, 0) + 1
        print(f"kluis: {tel or 'niets met volledige adressen'}")
    elif cmd == "show":
        ref = "origin/results:" + arg
        r = subprocess.run(["git", "show", ref], capture_output=True)
        if r.returncode == 0:
            sys.stdout.buffer.write(r.stdout)
            return
        sleutel()
        r = subprocess.run(["git", "show", ref + ".kluis"], capture_output=True, check=True)
        sys.stdout.buffer.write(ontsleutel(r.stdout))
    elif cmd == "check":
        slecht = [p for p in alle_bestanden(arg) if not p.endswith(OVERSLAAN) and gevoelig(p, lees(p))]
        for p in slecht:
            print("LEESBAAR:", p)
        print(f"kluis: {len(slecht)} bestanden met leesbare adressen")
        sys.exit(1 if slecht else 0)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
