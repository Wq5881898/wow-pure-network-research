"""Read-only realm/character listing with existing temporary loopback relays."""
import os
import subprocess
import threading
from pathlib import Path
from wsl_lab_connect import Relay, Server

def main():
    servers=[]
    try:
        for port in (3724,8085):
            server=Server(('127.0.0.1',port),Relay)
            servers.append(server)
            threading.Thread(target=server.serve_forever,daemon=True).start()
        cli=os.environ['AGENT_WOW_CLI']
        config=str(Path.home()/'wow-research/bridge-live/config.yaml')
        for args in (['realm','list'],['char','list','--json']):
            run=subprocess.run([cli,'--config',config,*args],capture_output=True,text=True,timeout=35)
            print('command',args,'exit',run.returncode)
            print(run.stdout)
            print(run.stderr)
            if run.returncode:
                raise SystemExit(run.returncode)
    finally:
        for server in servers:
            server.shutdown();server.server_close()

if __name__=='__main__':
    main()
