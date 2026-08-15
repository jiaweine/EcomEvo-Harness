from __future__ import annotations
import argparse
import uvicorn

def main():
    p=argparse.ArgumentParser(prog='ecomevo');p.add_argument('--host',default='0.0.0.0');p.add_argument('--port',type=int,default=8000);p.add_argument('--reload',action='store_true');a=p.parse_args()
    uvicorn.run('ecomevo.api.app:app',host=a.host,port=a.port,reload=a.reload)
if __name__=='__main__':main()
