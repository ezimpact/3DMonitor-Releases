"""Fetch the pinned, unmodified source archives for the separate preview dependency."""
from pathlib import Path
import argparse,hashlib,json,sys
import httpx

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--destination',type=Path,required=True);args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root/'work/k1-monitor'))
    from k1monitor.notifications import telegram_ssl_context
    manifest=json.loads((root/'THIRD_PARTY_SOURCES.json').read_text(encoding='utf-8'))
    args.destination.mkdir(parents=True,exist_ok=True)
    with httpx.Client(verify=telegram_ssl_context(),follow_redirects=True,timeout=120) as client:
        for item in manifest['archives']:
            path=args.destination/item['name']
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']:continue
            if path.exists():raise SystemExit('Existing dependency file differs: '+path.name)
            response=client.get(item['url']);response.raise_for_status()
            if hashlib.sha256(response.content).hexdigest()!=item['sha256']:raise SystemExit('Dependency hash differs: '+path.name)
            path.write_bytes(response.content)
    (args.destination/'README.txt').write_text((root/'THIRD_PARTY.md').read_text(encoding='utf-8'),encoding='utf-8')
    print('Pinned preview source archives verified.')

if __name__=='__main__':main()
