using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Web.Script.Serialization;
using Microsoft.Win32;

// This helper never contacts printers or terminates processes.
static class SetupHelper {
    static readonly string[] Settings={"config.json","data/printers.json","remote.json","printer-credentials.json","notifications.json","data/print-history.sqlite3","data/filament.sqlite3","data/temperature-watch.json","data/print-pricing.json","data/print-costs.sqlite3","data/printer-order.json","data/language.json","data/updates.json","data/installer.json","CameraSaver/config.json","CameraSaver/data/screensaver-backup.json","data/installer-screensaver.json"};
    static readonly string[] DesktopNames={"SCRNSAVE.EXE","ScreenSaveActive","ScreenSaverIsSecure","ScreenSaveTimeOut"};
    static readonly JavaScriptSerializer Json=new JavaScriptSerializer();
    [DllImport("user32.dll",SetLastError=true)] static extern bool SystemParametersInfo(uint action,uint param,IntPtr data,uint flags);
    static string Root(string path){
        string full=Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar);
        if(full.Equals(Path.GetPathRoot(full).TrimEnd('\\'),StringComparison.OrdinalIgnoreCase))throw new Exception("Нельзя устанавливать программу в корень диска.");
        return full;
    }
    static bool Same(string a,string b){return !string.IsNullOrEmpty(a)&&!string.IsNullOrEmpty(b)&&Root(a).Equals(Root(b),StringComparison.OrdinalIgnoreCase);}
    static string Child(string root,string name){
        string path=Path.GetFullPath(Path.Combine(root,name.Replace('/',Path.DirectorySeparatorChar)));
        if(!path.StartsWith(root+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase))throw new Exception("Некорректный путь настройки.");
        return path;
    }
    static void CheckStopped(string root){
        foreach(string name in new[]{"K1Monitor","3DMonitor","CrealityCameraSaver","CameraReceiver"})foreach(var process in Process.GetProcessesByName(name))using(process){
            try{
                string path=process.MainModule.FileName;
                if(path.StartsWith(root+"\\",StringComparison.OrdinalIgnoreCase))throw new Exception("Закройте 3DMonitor через пункт «Остановить службу» в трее и закройте просмотр заставки. Печать на принтере продолжится.");
            }catch(System.ComponentModel.Win32Exception){throw new Exception("Не удалось проверить запущенный процесс "+name+". Закройте его перед установкой.");}
            catch(InvalidOperationException){}
        }
    }
    static Dictionary<string,object> ReadObject(string path){return Json.Deserialize<Dictionary<string,object>>(File.ReadAllText(path,Encoding.UTF8));}
    static void ValidateSource(string source){
        if((!File.Exists(Child(source,"K1Monitor.exe"))&&!File.Exists(Child(source,"3DMonitor.exe")))||!File.Exists(Child(source,"config.json"))||!File.Exists(Child(source,"data/printers.json")))throw new Exception("Выберите portable-папку 3DMonitor с K1Monitor.exe, config.json и data/printers.json.");
        var registry=ReadObject(Child(source,"data/printers.json"));if(!registry.ContainsKey("printers"))throw new Exception("В папке не найдена корректная база принтеров.");
        var config=ReadObject(Child(source,"config.json"));object location;
        if(config.TryGetValue("registry_path",out location)&&Convert.ToString(location).Replace('\\','/')!="data/printers.json")throw new Exception("Для переноса нужна база data/printers.json внутри portable-папки. Внешнюю базу сначала перенесите в эту папку.");
        foreach(string name in Settings)if(File.Exists(Child(source,name))&&name.EndsWith(".json",StringComparison.OrdinalIgnoreCase))ReadObject(Child(source,name));
    }
    static void Copy(string source,string destination){Directory.CreateDirectory(Path.GetDirectoryName(destination));File.Copy(source,destination,false);}
    static void Prepare(string target,string source){
        target=Root(target);CheckStopped(target);
        if(Directory.Exists(target)&&Directory.EnumerateFileSystemEntries(target).Any()&&(!File.Exists(Child(target,"K1Monitor.exe"))&&!File.Exists(Child(target,"3DMonitor.exe")))){
            // Uninstall deliberately retains settings. Allow a later reinstall into that folder.
            string retained=Child(target,"data/installer.json");
            if(!File.Exists(retained))retained=Child(target,"data/installer-screensaver.json");
            if(!File.Exists(retained)||!File.Exists(Child(target,"config.json"))||!File.Exists(Child(target,"data/printers.json")))throw new Exception("Выберите пустую папку или существующую папку 3DMonitor.");
            var saved=ReadObject(retained);
            if(!(saved.ContainsKey("app")&&Convert.ToString(saved["app"])=="3DMonitor")&&(!saved.ContainsKey("previous_application")||DesktopNames.Any(name=>!saved.ContainsKey(name))))throw new Exception("В выбранной папке не найдены данные прежней установки 3DMonitor.");
            ReadObject(Child(target,"config.json"));ReadObject(Child(target,"data/printers.json"));
        }
        bool migrate=!string.IsNullOrWhiteSpace(source)&&!Same(target,source);
        if(migrate){
            source=Root(source);ValidateSource(source);CheckStopped(source);
            if(File.Exists(Child(target,"config.json")))throw new Exception("В выбранной папке уже есть настройки. Обновление сохранит их; импорт в эту папку отключён.");
        }
        var existing=Settings.Where(name=>File.Exists(Child(target,name))).ToList();
        if(existing.Count>0){
            string backup=Child(target,"_update-backups/"+DateTime.Now.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,8));
            foreach(string name in existing)Copy(Child(target,name),Child(backup,name));
        }
        if(migrate){
            var created=new List<string>();
            try{foreach(string name in Settings)if(File.Exists(Child(source,name))){string dest=Child(target,name);Copy(Child(source,name),dest);created.Add(dest);}}
            catch{foreach(string path in created)File.Delete(path);throw;}
        }
        RemoveLegacySaver(target);
        string state=Child(target,"data/installer.json");
        Directory.CreateDirectory(Path.GetDirectoryName(state));
        if(!File.Exists(state))File.WriteAllText(state,Json.Serialize(new Dictionary<string,object>{{"app","3DMonitor"},{"schema_version",1}}),new UTF8Encoding(false));
    }
    static void RemoveLegacySaver(string target){
        string directory=Child(target,"CameraSaver");if(!Directory.Exists(directory))return;
        string executable=Child(target,"CameraSaver/CrealityCameraSaver.exe");
        if(Same(CurrentSaver(),executable)){
            string current="";using(var desktop=Registry.CurrentUser.OpenSubKey(@"Control Panel\Desktop"))if(desktop!=null)current=Convert.ToString(desktop.GetValue("SCRNSAVE.EXE",""));
            string filename=Path.GetFileName(current).ToLowerInvariant();
            if(filename=="crealitycamerasaver.scr"||filename=="creali~1.scr"){
                string backup=Child(target,"CameraSaver/data/screensaver-backup.json");
                if(!File.Exists(backup))throw new Exception("Нельзя удалить активную заставку без резервной копии. Сначала выберите другую заставку в настройках Windows.");
                var saved=ReadObject(backup);if(DesktopNames.Any(name=>!saved.ContainsKey(name)))throw new Exception("Не удалось восстановить прежнюю заставку. Выберите её в настройках Windows перед удалением программы.");
                RestoreDesktop(saved);
            }
            using(var own=Registry.CurrentUser.OpenSubKey(@"Software\CrealityCameraSaver",true))if(own!=null)own.DeleteValue("ApplicationPath",false);
        }
        if((File.GetAttributes(directory)&FileAttributes.ReparsePoint)!=0)throw new Exception("Некорректный путь настройки.");
        string archived=Child(target,"_removed-components/CameraSaver-"+DateTime.UtcNow.ToString("yyyyMMdd-HHmmss")+"-"+Guid.NewGuid().ToString("N").Substring(0,8));
        Directory.CreateDirectory(Path.GetDirectoryName(archived));Directory.Move(directory,archived);
    }
    static string CurrentSaver(){using(var own=Registry.CurrentUser.OpenSubKey(@"Software\CrealityCameraSaver"))return own==null?"":Convert.ToString(own.GetValue("ApplicationPath",""));}
    static void RestoreDesktop(Dictionary<string,object> saved){
        Action write=()=>{using(var key=Registry.CurrentUser.OpenSubKey(@"Control Panel\Desktop",true))foreach(string name in DesktopNames){
            var entry=(Dictionary<string,object>)saved[name];if(Convert.ToBoolean(entry["present"]))key.SetValue(name,entry["value"],(RegistryValueKind)Enum.Parse(typeof(RegistryValueKind),(string)entry["kind"]));else key.DeleteValue(name,false);
        }};
        write();
        var timeout=(Dictionary<string,object>)saved["ScreenSaveTimeOut"];var active=(Dictionary<string,object>)saved["ScreenSaveActive"];var secure=(Dictionary<string,object>)saved["ScreenSaverIsSecure"];
        foreach(var pair in new[]{Tuple.Create(0x0fu,Convert.ToBoolean(timeout["present"])?Convert.ToUInt32(timeout["value"]):600u),Tuple.Create(0x11u,Convert.ToString(active["value"])=="1"?1u:0u),Tuple.Create(0x77u,Convert.ToString(secure["value"])=="1"?1u:0u)})
            if(!SystemParametersInfo(pair.Item1,pair.Item2,IntPtr.Zero,3))throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
        write();
    }
    static void BeforeUninstall(string target){CheckStopped(Root(target));}
    static string LocalizedError(string message,string language){
        if(language!="en")return message;
        var messages=new Dictionary<string,string>{
            {"Нельзя устанавливать программу в корень диска.","Do not install the application in the root of a drive."},
            {"Некорректный путь настройки.","Invalid settings path."},
            {"Закройте 3DMonitor через пункт «Остановить службу» в трее и закройте просмотр заставки. Печать на принтере продолжится.","Close 3DMonitor using “Stop application” in its tray menu and close the screensaver viewer. Printing on the printer will continue."},
            {"Не удалось проверить запущенный процесс ","Could not check the running process "},
            {". Закройте его перед установкой.",". Close it before installing."},
            {"Выберите portable-папку 3DMonitor с K1Monitor.exe, config.json и data/printers.json.","Choose a portable 3DMonitor folder with 3DMonitor.exe (or K1Monitor.exe), config.json and data/printers.json."},
            {"В папке не найдена корректная база принтеров.","No valid printer database found in this folder."},
            {"Для переноса нужна база data/printers.json внутри portable-папки. Внешнюю базу сначала перенесите в эту папку.","Migration requires data/printers.json inside the portable folder. Move an external database into this folder first."},
            {"Выберите пустую папку или существующую папку 3DMonitor.","Choose an empty folder or an existing 3DMonitor folder."},
            {"В выбранной папке не найдены данные прежней установки 3DMonitor.","Previous 3DMonitor installation data was not found in the selected folder."},
            {"В выбранной папке уже есть настройки. Обновление сохранит их; импорт в эту папку отключён.","The selected folder already contains settings. An update preserves them; importing into this folder is disabled."},
            {"Нельзя удалить активную заставку без резервной копии. Сначала выберите другую заставку в настройках Windows.","Cannot remove an active screensaver without a backup. Select another screensaver in Windows settings first."},
            {"Не удалось восстановить прежнюю заставку. Выберите её в настройках Windows перед удалением программы.","Could not restore the previous screensaver. Select it in Windows settings before uninstalling."},
            {"Нет ресурса запускателя.","Launcher resource missing."},
            {"Некорректные параметры установщика.","Invalid installer parameters."},
            {"Неизвестное действие установщика.","Unknown installer action."}
        };
        string exact;if(messages.TryGetValue(message,out exact))return exact;
        foreach(var pair in messages)message=message.Replace(pair.Key,pair.Value);
        return message;
    }
    [STAThread] static int Main(string[] args){
        try{
            if(args.Length!=4&&args.Length!=5)throw new Exception("Некорректные параметры установщика.");
            if(args[0]=="prepare")Prepare(args[1],args[2]);else if(args[0]=="uninstall")BeforeUninstall(args[1]);else throw new Exception("Неизвестное действие установщика.");
            return 0;
        }catch(Exception ex){if(args.Length>=4)File.WriteAllText(args[3],LocalizedError(ex.Message,args.Length==5?args[4]:"ru"),new UnicodeEncoding(false,true));return 1;}
    }
}
