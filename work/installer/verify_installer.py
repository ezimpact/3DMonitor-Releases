"""Exercise the real installer with synthetic settings and a separate HKCU namespace.

Never start a monitor with configured printers, register a saver, or stop the live app.
Test registry entries/shortcuts are removed by the tested uninstaller. Fixtures remain.
"""
from pathlib import Path
import datetime,hashlib,json,lzma,os,shutil,struct,subprocess,sys,time,winreg
sys.stdout.reconfigure(encoding='utf-8')

workspace=Path(__file__).resolve().parents[2]
build=Path(__file__).resolve().parent
setup=workspace/'outputs/Public/3DMonitor-Setup-v0.101.exe'
qa=build/('qa '+datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
qa.mkdir(exist_ok=True)
target=qa/'установленная программа'
source=qa/'portable исходник'
migrated=qa/'перенесённая программа'
runkey=r'Software\Microsoft\Windows\CurrentVersion\Run'
testkey=r'Software\K1MonitorInstallerTest'
uninstallkey=r'Software\Microsoft\Windows\CurrentVersion\Uninstall\K1MonitorInstallerTest'
checks=[]

def check(value,label):
    assert value,label
    checks.append(label)
    print('PASS '+label,flush=True)

def reg(key,name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key,0,winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as handle:
            return winreg.QueryValueEx(handle,name)
    except FileNotFoundError:return None

def global_state():
    keys=[(r'Control Panel\Desktop',name) for name in ('SCRNSAVE.EXE','ScreenSaveActive','ScreenSaverIsSecure','ScreenSaveTimeOut')]
    keys += [(r'Software\CrealityCameraSaver',name) for name in ('ApplicationPath','RegistryPath')]
    keys += [(runkey,'K1Monitor'),(r'Software\K1Monitor','InstallDir'),(r'Software\Microsoft\Windows\CurrentVersion\Uninstall\K1Monitor','DisplayVersion')]
    live=workspace/'outputs/3DMonitor-v0.100.4'
    if not (live/'config.json').exists():live=workspace/'outputs/3DMonitor-v0.100.2'
    files=['config.json','data/printers.json','data/printer-order.json','data/language.json','data/updates.json','remote.json','printer-credentials.json','CameraSaver/config.json','CameraSaver/data/screensaver-backup.json','notifications.json','data/print-history.sqlite3']
    return {'registry':{key+'::'+name:reg(key,name) for key,name in keys},'live_settings':{name:hashlib.sha256((live/name).read_bytes()).hexdigest() if (live/name).is_file() else None for name in files if name not in ('data/print-history.sqlite3','notifications.json')},'telegram_preferences':{k:v for k,v in json.loads((live/'notifications.json').read_text(encoding='utf-8-sig')).items() if k!='update_offset'} if (live/'notifications.json').exists() else None}

def install(folder,*options,expected=0):
    assert folder.resolve().is_relative_to(qa.resolve())
    # NSIS requires /D= to be the last argument, without quotes even with spaces.
    # FileFunc options require /KEY="value with spaces", not "/KEY=value".
    parameters=['/S','/TESTMODE','/TESTDIR='+subprocess.list2cmdline([str(folder)])]
    for option in options:
        key,value=option.split('=',1)
        parameters.append(key+'='+subprocess.list2cmdline([value]))
    command=subprocess.list2cmdline([str(setup)])+' '+' '.join(parameters)+' /D='+str(folder)
    result=subprocess.run(command,timeout=90)
    check(result.returncode==expected,'installer exit '+str(expected)+' for '+folder.name)

def uninstall(folder,expected=0):
    assert folder.resolve().is_relative_to(qa.resolve())
    # _?= keeps the original uninstaller running synchronously in this known fixture.
    command=subprocess.list2cmdline([str(folder/'Uninstall.exe'),'/S'])+' _?='+str(folder)
    result=subprocess.run(command,timeout=60)
    check(result.returncode==expected,'uninstaller exit '+str(expected)+' for '+folder.name)

def json_write(folder,name,value):
    path=folder/name;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def seed(folder):
    values={
        'config.json':{'schema_version':4,'registry_path':'data/printers.json','poll_seconds':7,'stale_after_seconds':21},
        'data/printers.json':{'schema_version':1,'printers':[{'id':'fixture','name':'Тест — НЕ настоящий принтер','ip':'192.168.254.250','protocol':'creality','auto_unload':True,'unload_temperature':234}]},
        'data/printer-order.json':{'schema_version':1,'ids':['fixture']},
        'data/language.json':{'schema_version':1,'language':'ru'},
        'data/updates.json':{'schema_version':1,'auto_check':False,'token':'SYNTHETIC-ENCRYPTED-NOT-A-TOKEN'},
        'remote.json':{'fixture_pairing':'synthetic-only','device_name':'Тестовое устройство'},
        'printer-credentials.json':{'fixture_secret':'SYNTHETIC-NEVER-SENT'},
        'notifications.json':{'windows':False,'telegram':False,'token':'SYNTHETIC-NOT-A-TOKEN','chats':[{'id':123,'name':'Fixture'}]},
        'CameraSaver/config.json':{'version':3,'wait_minutes':9,'registry_path':'../data/printers.json','disabled_printers':['fixture']},
        'CameraSaver/data/screensaver-backup.json':{'fixture_only':True},
    }
    for name,value in values.items():json_write(folder,name,value)
    import sqlite3
    history=folder/'data/print-history.sqlite3'
    with sqlite3.connect(history) as database:database.execute('CREATE TABLE IF NOT EXISTS fixture(value TEXT)');database.execute("INSERT INTO fixture VALUES ('synthetic history')")
    database.close()
    return {name:(folder/name).read_bytes() for name in [*values,'data/print-history.sqlite3']}

def same_settings(folder,saved,label):
    def retained(name):
        path=folder/name
        if path.exists():return path.read_bytes()
        if name.startswith('CameraSaver/'):
            found=list((folder/'_removed-components').glob('CameraSaver-*/'+name[len('CameraSaver/'):]))
            if found:return found[-1].read_bytes()
        return None
    check(all(retained(name)==value for name,value in saved.items()),label)

def pause_executable(folder):
    folder.mkdir(exist_ok=True)
    script=qa/'PauseFixture.cs'
    script.write_text('using System.Threading; class PauseFixture {static void Main(){Thread.Sleep(120000);}}',encoding='utf-8')
    compiler=Path(os.environ['WINDIR'])/'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
    exe=folder/'3DMonitor.exe'
    assert exe.resolve().is_relative_to(qa.resolve())
    subprocess.run([str(compiler),'/nologo','/target:winexe','/platform:x64','/out:'+str(exe),str(script)],check=True)
    process=subprocess.Popen([str(exe)])
    time.sleep(.4)
    return process

assert reg(testkey,'InstallDir') is None,'A previous test install exists; inspect it before running again.'
assert not target.exists() and not source.exists() and not migrated.exists(),'Use a fresh QA directory.'
before=global_state()
# Inspect the actual solid-LZMA NSIS header, rather than trusting source encoding.
# File layout: NSIS Source/exehead/fileform.h and fileform.c (firstheader/datablock).
binary=setup.read_bytes()
offset=binary.find(bytes.fromhex('efbeadde')+b'NullsoftInst')-4
assert offset>=0 and offset%512==0,'NSIS firstheader not found'
header_length=struct.unpack_from('<7I',binary,offset)[5]
stream=offset+28
properties=binary[stream:stream+5]
remainder=properties[0]//9
filters=[{'id':lzma.FILTER_LZMA1,'lc':properties[0]%9,'lp':remainder%5,'pb':remainder//5,'dict_size':int.from_bytes(properties[1:],'little')}]
decoded=lzma.LZMADecompressor(format=lzma.FORMAT_RAW,filters=filters).decompress(binary[stream+5:],max_length=header_length+4)
assert len(decoded)==header_length+4 and int.from_bytes(decoded[:4],'little')==header_length
compiled=decoded[4:]
russian_strings=[
    'Настройки установки',
    'Сохранение данных и запуск программы',
    'Запускать 3DMonitor при входе в Windows',
    'Программа устанавливается для текущего пользователя.',
    'Открыть 3DMonitor',
]
for text in russian_strings:check(text.encode('utf-16-le') in compiled,'correct Cyrillic in compiled installer: '+text)
check(all(text.encode('utf-16-le') not in compiled for text in ['Перенести настройки из portable-папки (первая установка)','Выберите portable-папку K1Monitor','Выберите portable-папку или снимите галочку переноса.']),'portable migration controls and related prompts removed from compiled installer')
desktop=Path(reg(r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders','Desktop')[0].replace('%USERPROFILE%',os.environ['USERPROFILE']))
menu=Path(os.environ['APPDATA'])/'Microsoft/Windows/Start Menu/Programs/K1Monitor Installer Test'
desktoplink=desktop/'K1Monitor Installer Test.lnk'
assert not menu.exists() and not desktoplink.exists(),'Test shortcut names already exist.'

install(target)
sys.path.insert(0,str(workspace/'work/k1-monitor'))
from k1monitor.language import windows_language
check(json.loads((target/'data/language.json').read_text())['language']==windows_language(),'installer default matches Windows display language')
check(reg(runkey,'K1MonitorInstallerTest') is None,'autostart is disabled by default')
check(reg(testkey,'InstallDir')[0]==str(target),'explicit destination with spaces')
check(reg(uninstallkey,'DisplayVersion')[0]=='0.101','per-user uninstall registration')
check(desktoplink.is_file() and (menu/'3DMonitor.lnk').is_file(),'desktop and Start menu shortcuts')
check((menu/'Описание программы.lnk').is_file() and (menu/'Удалить 3DMonitor.lnk').is_file(),'correct Cyrillic filenames for installed shortcuts')
check(json.loads((target/'data/printers.json').read_text())['printers']==[],'empty distributed printer registry')
check((target/'ModelPreview/occt-import-js.wasm').read_bytes()[:4]==b'\x00asm' and (target/'ModelPreview/three.module.js').is_file(),'local STL/STEP renderer installed as replaceable modules')
check((target/'ThirdParty/model-preview/occt-d2abb6d-source.tar.gz').is_file() and (target/'ThirdParty/model-preview/occt-import-js-0.0.23-source.tgz').is_file() and (target/'ModelPreview/LICENSE_LGPL_21.txt').is_file(),'corresponding third-party source and licences included')
check(not any(p.suffix.lower() in ('.py','.cs') for p in target.rglob('*')),'Python/C# source files excluded')
check(not (target/'CameraSaver/source').exists() and not (target/'remote.json').exists() and not (target/'printer-credentials.json').exists(),'source directory and private credentials excluded')
check(not (target/'CameraSaver').exists() and not any('CameraSaver' in p.parts for p in (build/'payload').rglob('*')),'screensaver and system launcher excluded from installer')
profile=qa/'empty user profile';profile.mkdir()
environment={**os.environ,'LOCALAPPDATA':str(profile),'APPDATA':str(profile)}
diagnose=subprocess.run([str(target/'3DMonitor.exe'),'--diagnose','1','--no-browser'],env=environment,timeout=60)
check(diagnose.returncode==2 and json.loads((target/'logs/diagnostic.json').read_text())['printers']==[],'installed EXE starts in diagnostic mode with zero printers')

saved=seed(target)
shutil.copy2(target/'3DMonitor.exe',target/'K1Monitor.exe')
with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,runkey,0,winreg.KEY_WRITE|winreg.KEY_WOW64_64KEY) as handle:
    winreg.SetValueEx(handle,'K1MonitorInstallerTest',0,winreg.REG_SZ,'"'+str(target/'K1Monitor.exe')+'" --no-browser')
install(target)
check(not (target/'K1Monitor.exe').exists(),'legacy K1Monitor binary replaced without duplicate application')
check(reg(runkey,'K1MonitorInstallerTest')[0]=='"'+str(target/'3DMonitor.exe')+'" --no-browser','legacy autostart preserved and retargeted to 3DMonitor')
install(target,'/AUTOSTART=1')
same_settings(target,saved,'update preserves database, pairing, credentials, unload and saver settings byte for byte')
expectedrun='"'+str(target/'3DMonitor.exe')+'" --no-browser'
check(reg(runkey,'K1MonitorInstallerTest')[0]==expectedrun,'enabled autostart uses quoted executable without opening browser')
backups=list((target/'_update-backups').iterdir())
check(any(all((backup/name).is_file() and (backup/name).read_bytes()==value for name,value in saved.items()) for backup in backups),'update creates a complete settings backup')
install(target)
check(reg(runkey,'K1MonitorInstallerTest')[0]==expectedrun,'update retains existing autostart choice')
same_settings(target,saved,'second update still preserves all settings')
check(not (target/'CameraSaver').exists() and len(list((target/'_removed-components').glob('CameraSaver-*')))==1,'old screensaver preferences archived without installing a screensaver')
install(target,'/AUTOSTART=0')
check(reg(runkey,'K1MonitorInstallerTest') is None,'autostart can be disabled on update')

foreign=qa/'foreign nonempty folder';foreign.mkdir();(foreign/'keep.txt').write_text('KEEP',encoding='utf-8')
install(foreign,expected=1)
check((foreign/'keep.txt').read_text()=='KEEP' and len(list(foreign.iterdir()))==1,'unrelated nonempty directory is untouched')
check(reg(testkey,'InstallDir')[0]==str(target),'failed install preserves existing installer registration')

blocked=qa/'running fixture';process=pause_executable(blocked)
try:
    install(blocked,expected=1)
    check(process.poll() is None and not (blocked/'data').exists(),'running target blocks install without termination or data changes')
finally:
    # Exact Popen object is an isolated sleeping fixture; never a real monitor.
    process.terminate();process.wait(timeout=10)

original_exe=(target/'3DMonitor.exe').read_bytes()
process=pause_executable(target)
try:
    uninstall(target,expected=1)
    check(process.poll() is None and (target/'Uninstall.exe').is_file(),'running target blocks uninstall without terminating it')
    same_settings(target,saved,'blocked uninstall leaves private settings unchanged')
finally:
    process.terminate();process.wait(timeout=10)
    (target/'3DMonitor.exe').write_bytes(original_exe)

uninstall(target)
same_settings(target,saved,'uninstall preserves all private settings')
check(not (target/'3DMonitor.exe').exists() and not (target/'CameraSaver/CrealityCameraSaver.exe').exists(),'uninstall removes installed program binaries')
check((target/'_update-backups').is_dir() and (target/'logs/diagnostic.json').is_file(),'uninstall preserves backups and logs')
check(reg(testkey,'InstallDir') is None and reg(uninstallkey,'DisplayVersion') is None and reg(runkey,'K1MonitorInstallerTest') is None,'uninstall removes only test registration and autostart')
check(not menu.exists() and not desktoplink.exists(),'uninstall removes test shortcuts')

install(target,'/AUTOSTART=1')
same_settings(target,saved,'reinstall after uninstall preserves retained database and pairing')
check((target/'3DMonitor.exe').is_file(),'reinstall restores program binaries')
uninstall(target)
same_settings(target,saved,'second uninstall still preserves settings')

source.mkdir();shutil.copy2(build/'payload/3DMonitor.exe',source/'K1Monitor.exe')
portable=seed(source)
install(migrated,'/AUTOSTART=1','/MIGRATE='+str(source))
same_settings(migrated,portable,'portable migration preserves all private settings byte for byte')
check(not (migrated/'CameraSaver').exists(),'migration keeps old screensaver in backup only')
same_settings(source,portable,'portable migration leaves source settings unchanged')
check(reg(testkey,'InstallDir')[0]==str(migrated),'migration uses the chosen fresh installation directory')
uninstall(migrated)
same_settings(migrated,portable,'uninstall after migration preserves settings')

for text in ['Installation options','Keep your data and configure startup','Start 3DMonitor when signing in to Windows','Open 3DMonitor']:
    check(text.encode('utf-16-le') in compiled,'English text in compiled installer: '+text)
english=qa/'English language install'
install(english,'/TESTLANG=en')
check(json.loads((english/'data/language.json').read_text())['language']=='en','English installer seeds English application default')
check((menu/'About 3DMonitor.lnk').exists() and (menu/'Uninstall 3DMonitor.lnk').exists(),'English shortcuts created')
json_write(english,'data/language.json',{'schema_version':1,'language':'ru'})
language_bytes=(english/'data/language.json').read_bytes()
install(english,'/TESTLANG=en')
check((english/'data/language.json').read_bytes()==language_bytes,'manual application language preserved during English installer update')
uninstall(english)
check((english/'data/language.json').read_bytes()==language_bytes,'manual application language retained after uninstall')
install(english,'/TESTLANG=en')
check((english/'data/language.json').read_bytes()==language_bytes,'reinstall does not replace manual language with Windows default')
uninstall(english)

check(global_state()==before,'live settings, real autostart and Windows saver registration are unchanged')
check(reg(testkey,'InstallDir') is None and not menu.exists() and not desktoplink.exists(),'test registry and shortcuts fully cleaned up')
report={'checks':len(checks),'passed':checks,'setup_sha256':hashlib.sha256(setup.read_bytes()).hexdigest(),'setup_size_bytes':setup.stat().st_size,'no_printer_connections_or_commands':True,'live_state_unchanged':True,'active_saver_restore_not_exercised':True,'interactive_ui_verified':False,'ui_verification_limitation':'Native interactive wizard not exercised; compiled strings verified','fixture_directory':str(qa)}
(build/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({key:value for key,value in report.items() if key!='passed'},ensure_ascii=False),flush=True)


