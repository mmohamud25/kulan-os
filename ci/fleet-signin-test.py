"""Kulan Fleet's sign-in accepts the code, the email's link, and the address
the browser landed on, however it was copied (no http://, only the part after
"#", line breaks from a phone); anything else is refused before sending."""
import sys, types, importlib.machinery, urllib.parse
src=open(sys.argv[1]).read()
start=src.index("    def verify("); end=src.index("    def token(")
body=src[start:end]
ns={"urllib":urllib,"FleetError":Exception,"SUPABASE_URL":"https://x.supabase.co"}
calls=[]
ns["http"]=lambda url, data, *a: calls.append((url,data)) or {"user":{"email":"a@b.c"}}
exec("class S:\n    email = \"\"\n    def _take(self, d): pass\n    def sign_out(self): pass\n" + body, ns)
S=ns["S"]
expect = []
for c in ["http://localhost:3000/#access_token=x&refresh_token=R1&sb=&token_type=bearer&type=signup",
          "localhost:3000/#access_token=x&refresh_token=R2&type=signup",
          "#access_token=x&refresh_token=R3&type=signup",
          "access_token=x&refresh_token=R4&type=signup",
          "http://localhost:3000/#access_token=x&refresh_\ntoken=R5&type=signup ",
          "https://x.supabase.co/auth/v1/verify?token=T6&type=magiclink&redirect_to=http://localhost:3000",
          "123456"]:
    calls.clear(); S().verify("a@b.c", c); assert calls, c
    expect.append(calls[-1])
for bad in ["hello", "localhost:3000"]:
    calls.clear()
    try:
        S().verify("a@b.c", bad)
    except Exception:
        assert not calls, bad
    else:
        raise SystemExit(f"accepted {bad!r}")
tokens = [d.get("refresh_token") or d.get("token_hash") or d.get("token") for _u, d in expect]
assert tokens == ["R1", "R2", "R3", "R4", "R5", "T6", "123456"], tokens
print("sign-in forms ok")
