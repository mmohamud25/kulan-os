import os, pty, sys, time, select
rc, mode = sys.argv[1], sys.argv[2]
home = os.path.abspath("h"); os.makedirs(home, exist_ok=True)
open(f"{home}/.zshrc","w").write(f"source {rc}\n")
pid, fd = pty.fork()
if pid == 0:
    os.environ.update(HOME=home, TERM="xterm-256color", ZDOTDIR=home)
    os.execvp("zsh", ["zsh", "-i"])
def read(t=1.5):
    out=b""; end=time.time()+t
    while time.time()<end:
        r,_,_=select.select([fd],[],[],0.1)
        if r:
            try: out+=os.read(fd,65536)
            except OSError: break
    return out
P=b"\x1b[200~echo A1\necho A2\x1b[201~"
if mode=="ahead": os.write(fd,P); read(3)
elif mode=="slow":
    read(3)
    for ch in P: os.write(fd,bytes([ch])); time.sleep(0.003)
    read(1)
elif mode=="typed":
    read(3); os.write(fd,b"ec"); read(0.5); os.write(fd,P); read(1)
elif mode=="aftercmd":
    read(3); os.write(fd,b"true\r"); read(1); os.write(fd,P); read(1)
os.write(fd,b"\r"); o=read(2)
os.write(fd,b"exit\r"); read(1)
bad = b"200~" in o.replace(b"\x1b[?2004h",b"").replace(b"\x1b[?2004l",b"") or b"bad pattern" in o
print(mode, "BROKEN" if bad else "ok", repr(o[-160:]) if bad else "")
