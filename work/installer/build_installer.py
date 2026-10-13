"""Create a clean installer payload. Keep the live installation untouched."""
from pathlib import Path
import argparse,hashlib,json,os,shutil,subprocess
parser=argparse.ArgumentParser();parser.add_argument("--nsis",type=Path,required=True);args=parser.parse_args();nsis=args.nsis.resolve()
assert nsis.is_file(),"NSIS compiler not found"
root=Path(__file__).resolve().parents[2];work=Path(__file__).resolve().parent
release=root/'outputs/3DMonitor-v0.101';payload=work/'payload';payload.mkdir(exist_ok=True)
if any(payload.iterdir()):
    manifest=work/'payload-manifest.json'
    assert manifest.is_file(),'Nonempty staging folder without a build manifest.'
    known=set(json.loads(manifest.read_text(encoding='utf-8'))['files'])
    previous=list(payload.rglob('*'))
    assert payload.resolve().parent==work.resolve() and not payload.is_junction()
    assert not any(p.is_symlink() or p.is_junction() for p in previous),'Links are not allowed in generated staging.'
    assert {str(p.relative_to(payload)) for p in previous if p.is_file()}==known,'Unexpected files in staging; nothing removed.'
    for path in previous:
        assert path.resolve().is_relative_to(payload.resolve())
    for path in previous:
        if path.is_file():path.unlink()
    for path in sorted((p for p in previous if p.is_dir()),key=lambda p:-len(p.parts)):path.rmdir()
root_files=['3DMonitor.exe','readme.txt','THIRD_PARTY_NOTICES.txt','Stop-Monitor.cmd','Stop-Monitor.ps1','Enable-Remote.cmd','Enable-Remote.ps1']
for name in root_files:shutil.copy2(release/name,payload/name)
for folder in ['ModelPreview','ThirdParty']:
    shutil.copytree(release/folder,payload/folder)
shutil.copy2(root/'work/k1-monitor/k1monitor/static/k1monitor.ico',payload/'app.ico')
shutil.copy2(work/'NSIS-NOTICES.txt',payload/'NSIS-LICENSE.txt')
compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/optimize+','/reference:System.Web.Extensions.dll','/out:'+str(payload/'K1SetupHelper.exe'),str(work/'SetupHelper.cs')],check=True)
config={'registry_path':'data/printers.json','poll_seconds':5,'stale_after_seconds':15}
defaults={'config.json':config,'data/printers.json':{'schema_version':1,'printers':[]}}
for name,value in defaults.items():
    target=payload/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(json.dumps(value,indent=2),encoding='utf-8')
files=sorted(p for p in payload.rglob('*') if p.is_file());install=[];uninstall=[]
for path in files:
    relative=path.relative_to(payload);name=str(relative)
    install.append(f'SetOutPath "$INSTDIR\\{relative.parent}"' if str(relative.parent)!='.' else 'SetOutPath "$INSTDIR"')
    if relative.as_posix() in defaults:install.append('SetOverwrite off')
    install.append(f'File "payload\\{name}"')
    if relative.as_posix() in defaults:install.append('SetOverwrite on')
    else:uninstall.append(f'Delete "$INSTDIR\\{name}"')
directories=sorted({parent.relative_to(payload) for p in files for parent in p.parents if parent.is_relative_to(payload) and parent!=payload},key=lambda p:-len(p.parts))
for relative in directories:uninstall.append(f'RMDir "$INSTDIR\\{relative}"')
uninstall.append('RMDir "$INSTDIR"')
(work/'payload-files.nsh').write_text('\n'.join(install)+'\n',encoding='utf-8-sig')
(work/'payload-delete.nsh').write_text('\n'.join(uninstall)+'\n',encoding='utf-8-sig')
assert not any(p.suffix.lower() in ('.py','.cs') for p in files)
assert not any(p.name in ('remote.json','printer-credentials.json','notifications.json','print-history.sqlite3') for p in files)
report={'version':'0.101','packager':'NSIS 3.13','source_files_excluded':True,'credentials_excluded':True,'empty_registry':True,'files':[str(p.relative_to(payload)) for p in files],'screensaver_excluded':True}
(work/'payload-manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
subprocess.run([str(nsis),'/V3','/INPUTCHARSET','UTF8',str(work/'K1Monitor.nsi')],check=True)
print(json.dumps({k:v for k,v in report.items() if k!='files'}))
