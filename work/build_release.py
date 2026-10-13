"""Build a clean Windows release from this source snapshot; never launch it."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess,sys

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--nsis',type=Path,required=True,help='Path to NSIS 3.13 makensis.exe')
    args=parser.parse_args()
    work=Path(__file__).resolve().parent;root=work.parent
    release=root/'outputs/3DMonitor-v0.101'
    if release.exists():raise SystemExit('Release directory already exists. Choose a fresh checkout; existing files are left untouched.')
    if not args.nsis.is_file():raise SystemExit('NSIS compiler not found.')
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','3DMonitor.spec'],cwd=work,check=True)
    release.mkdir(parents=True)
    shutil.copy2(work/'dist/3DMonitor.exe',release/'3DMonitor.exe')
    for name in ['Stop-Monitor.cmd','Stop-Monitor.ps1','Enable-Remote.cmd','Enable-Remote.ps1']:
        shutil.copy2(work/'k1-monitor'/name,release/name)
    shutil.copy2(root/'THIRD_PARTY_NOTICES.txt',release/'THIRD_PARTY_NOTICES.txt')
    shutil.copytree(work/'k1-monitor/k1monitor/static/vendor/model-preview',release/'ModelPreview')
    (release/'readme.txt').write_text('3DMonitor 0.101\nhttps://github.com/ezimpact/3DMonitor-Releases\nFeedback: https://t.me/ezimpact\n',encoding='utf-8')
    subprocess.run([sys.executable,str(work/'fetch_third_party_sources.py'),'--destination',str(release/'ThirdParty/model-preview')],check=True)
    (root/'outputs/Public').mkdir(exist_ok=True)
    subprocess.run([sys.executable,str(work/'installer/build_installer.py'),'--nsis',str(args.nsis.resolve())],check=True)
    setup=root/'outputs/Public/3DMonitor-Setup-v0.101.exe'
    digest=hashlib.sha256(setup.read_bytes()).hexdigest()
    setup.with_suffix('.sha256').write_text(digest+'  '+setup.name+'\n',encoding='utf-8')
    print(json.dumps({'installer':str(setup),'sha256':digest}))

if __name__=='__main__':main()
