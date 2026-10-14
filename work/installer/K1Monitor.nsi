Unicode true
!include "MUI2.nsh"
!include "nsDialogs.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!define VERSION "0.103.3"
Name "3DMonitor ${VERSION}"
OutFile "..\..\outputs\Public\3DMonitor-Setup-v${VERSION}.exe"
InstallDir "$LOCALAPPDATA\Programs\3DMonitor"
InstallDirRegKey HKCU "Software\K1Monitor" "InstallDir"
RequestExecutionLevel user
VIProductVersion "0.103.3.0"
VIAddVersionKey /LANG=1049 "ProductName" "3DMonitor"
VIAddVersionKey /LANG=1049 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "FileDescription" "Установщик 3DMonitor"
VIAddVersionKey /LANG=1049 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1049 "LegalCopyright" "EZIMPACT"
VIAddVersionKey /LANG=1033 "ProductName" "3DMonitor"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "FileDescription" "3DMonitor installer"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "EZIMPACT"
SetCompressor /SOLID lzma
SetOverwrite on
ShowInstDetails show
ShowUninstDetails show
Icon "payload\app.ico"
UninstallIcon "payload\app.ico"
!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\3DMonitor.exe"
!define MUI_FINISHPAGE_RUN_TEXT "$(OpenProgram)"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom OptionsPage OptionsLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Russian"
!include "languages.nsh"
Var AppKey
Var UninstallKey
Var RunName
Var MenuName
Var TestMode
Var StartupSelected
Var MigrationSource
Var StartupCheckbox
Var Dialog
Var AppLanguage

Function .onInit
  ; Use the Windows display language, not keyboard or regional formatting.
  System::Call 'kernel32::GetUserDefaultUILanguage() i.r0'
  IntOp $0 $0 & 0x3ff
  StrCpy $LANGUAGE ${LANG_ENGLISH}
  StrCpy $AppLanguage "en"
  ${If} $0 = 25
    StrCpy $LANGUAGE ${LANG_RUSSIAN}
    StrCpy $AppLanguage "ru"
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "$(Need64)" /SD IDOK
    Abort
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
  StrCpy $AppKey "Software\K1Monitor"
  StrCpy $UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\K1Monitor"
  StrCpy $RunName "K1Monitor"
  StrCpy $MenuName "3DMonitor"
  StrCpy $StartupSelected 0
  StrCpy $MigrationSource ""
  StrCpy $TestMode 0
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/TESTMODE" $1
  ${IfNot} ${Errors}
    StrCpy $TestMode 1
    StrCpy $AppKey "Software\K1MonitorInstallerTest"
    StrCpy $UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\K1MonitorInstallerTest"
    StrCpy $RunName "K1MonitorInstallerTest"
    StrCpy $MenuName "K1Monitor Installer Test"
  ${EndIf}
  ${If} $TestMode == 1
    ClearErrors
    ${GetOptions} $0 "/TESTLANG=" $1
    ${IfNot} ${Errors}
      ${If} $1 == "ru"
        StrCpy $LANGUAGE ${LANG_RUSSIAN}
        StrCpy $AppLanguage "ru"
      ${ElseIf} $1 == "en"
        StrCpy $LANGUAGE ${LANG_ENGLISH}
        StrCpy $AppLanguage "en"
      ${EndIf}
    ${EndIf}
    ClearErrors
    ${GetOptions} $0 "/TESTDIR=" $2
    ${If} ${Errors}
      MessageBox MB_ICONSTOP "$(TestDirectoryRequired)" /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
    StrCpy $INSTDIR $2
  ${EndIf}
  ReadRegStr $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $RunName
  ${If} $1 != ""
    StrCpy $StartupSelected 1
  ${EndIf}
  ClearErrors
  ${GetOptions} $0 "/AUTOSTART=" $1
  ${IfNot} ${Errors}
    StrCpy $StartupSelected $1
  ${EndIf}
  ClearErrors
  ${GetOptions} $0 "/MIGRATE=" $MigrationSource
  ClearErrors
FunctionEnd

Function OptionsPage
  !insertmacro MUI_HEADER_TEXT "$(OptionsTitle)" "$(OptionsSubtitle)"
  nsDialogs::Create 1018
  Pop $Dialog
  ${NSD_CreateLabel} 0 0 100% 25u "$(PreserveNote)"
  Pop $0
  ${NSD_CreateCheckbox} 0 32u 100% 12u "$(StartupLabel)"
  Pop $StartupCheckbox
  ${NSD_SetState} $StartupCheckbox $StartupSelected
  ${NSD_CreateLabel} 0 55u 100% 30u "$(PerUserNote)"
  Pop $0
  nsDialogs::Show
FunctionEnd
Function OptionsLeave
  ${NSD_GetState} $StartupCheckbox $StartupSelected
FunctionEnd

Section "3DMonitor"
  InitPluginsDir
  SetOutPath "$PLUGINSDIR"
  File "payload\K1SetupHelper.exe"
  StrCpy $0 1
  ClearErrors
  ExecWait '"$PLUGINSDIR\K1SetupHelper.exe" prepare "$INSTDIR" "$MigrationSource" "$PLUGINSDIR\error.txt" "$AppLanguage"' $0
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "$(PrepareFailed)" /SD IDOK
    SetErrorLevel 1
    Quit
  ${EndIf}
  ${If} $0 != 0
    FileOpen $1 "$PLUGINSDIR\error.txt" r
    FileReadUTF16LE $1 $2
    FileClose $1
    MessageBox MB_ICONSTOP "$2" /SD IDOK
    SetErrorLevel 1
    Quit
  ${EndIf}
  !include "payload-files.nsh"
  Delete "$INSTDIR\K1Monitor.exe"
  ${If} $TestMode != 1
  Delete "$SMPROGRAMS\K1Monitor\K1Monitor.lnk"
  Delete "$SMPROGRAMS\K1Monitor\Описание программы.lnk"
  Delete "$SMPROGRAMS\K1Monitor\Удалить K1Monitor.lnk"
  RMDir "$SMPROGRAMS\K1Monitor"
  Delete "$DESKTOP\K1Monitor.lnk"
  ${EndIf}
  ${If} $TestMode == 1
    FileOpen $0 "$INSTDIR\_installer-test.flag" w
    FileWrite $0 "1"
    FileClose $0
  ${EndIf}
  ; Set the app default once. An existing manual choice always wins on update.
  IfFileExists "$INSTDIR\data\language.json" language_ready
    FileOpen $0 "$INSTDIR\data\language.json" w
    FileWrite $0 '{"schema_version":1,"language":"$AppLanguage"}$\r$\n'
    FileClose $0
  language_ready:
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  Delete "$SMPROGRAMS\$MenuName\Описание программы.lnk"
  Delete "$SMPROGRAMS\$MenuName\About 3DMonitor.lnk"
  Delete "$SMPROGRAMS\$MenuName\Удалить 3DMonitor.lnk"
  Delete "$SMPROGRAMS\$MenuName\Uninstall 3DMonitor.lnk"
  CreateDirectory "$SMPROGRAMS\$MenuName"
  CreateShortcut "$SMPROGRAMS\$MenuName\3DMonitor.lnk" "$INSTDIR\3DMonitor.exe" "" "$INSTDIR\app.ico"
  CreateShortcut "$SMPROGRAMS\$MenuName\$(ReadmeShortcut).lnk" "$INSTDIR\readme.txt"
  CreateShortcut "$SMPROGRAMS\$MenuName\$(UninstallShortcut).lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\$MenuName.lnk" "$INSTDIR\3DMonitor.exe" "" "$INSTDIR\app.ico"
  WriteRegStr HKCU $AppKey "InstallDir" "$INSTDIR"
  WriteRegStr HKCU $UninstallKey "Publisher" "EZIMPACT"
  WriteRegStr HKCU $UninstallKey "DisplayName" "3DMonitor ${VERSION}"
  WriteRegStr HKCU $UninstallKey "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU $UninstallKey "DisplayIcon" "$INSTDIR\3DMonitor.exe"
  WriteRegStr HKCU $UninstallKey "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU $UninstallKey "QuietUninstallString" '$\"$INSTDIR\Uninstall.exe$\" /S'
  WriteRegDWORD HKCU $UninstallKey "NoModify" 1
  WriteRegDWORD HKCU $UninstallKey "NoRepair" 1
  ${If} $StartupSelected == 1
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $RunName '$\"$INSTDIR\3DMonitor.exe$\" --no-browser'
  ${Else}
    ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $RunName
    ${If} $0 == '$\"$INSTDIR\3DMonitor.exe$\" --no-browser'
    ${OrIf} $0 == '$\"$INSTDIR\K1Monitor.exe$\" --no-browser'
      DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $RunName
    ${EndIf}
  ${EndIf}
SectionEnd

Function un.onInit
  ; Use the Windows display language, not keyboard or regional formatting.
  System::Call 'kernel32::GetUserDefaultUILanguage() i.r0'
  IntOp $0 $0 & 0x3ff
  StrCpy $LANGUAGE ${LANG_ENGLISH}
  StrCpy $AppLanguage "en"
  ${If} $0 = 25
    StrCpy $LANGUAGE ${LANG_RUSSIAN}
    StrCpy $AppLanguage "ru"
  ${EndIf}
  SetShellVarContext current
  SetRegView 64
  StrCpy $AppKey "Software\K1Monitor"
  StrCpy $UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\K1Monitor"
  StrCpy $RunName "K1Monitor"
  StrCpy $MenuName "3DMonitor"
  IfFileExists "$INSTDIR\_installer-test.flag" test_uninstall uninstall_ready
  test_uninstall:
    StrCpy $AppKey "Software\K1MonitorInstallerTest"
    StrCpy $UninstallKey "Software\Microsoft\Windows\CurrentVersion\Uninstall\K1MonitorInstallerTest"
    StrCpy $RunName "K1MonitorInstallerTest"
    StrCpy $MenuName "K1Monitor Installer Test"
  uninstall_ready:
  IfFileExists "$INSTDIR\K1SetupHelper.exe" helper_ready
    MessageBox MB_ICONSTOP "$(HelperMissing)" /SD IDOK
    SetErrorLevel 1
    Abort
  helper_ready:
  Delete "$TEMP\3DMonitor-uninstall-error.txt"
  StrCpy $0 1
  ClearErrors
  ExecWait '"$INSTDIR\K1SetupHelper.exe" uninstall "$INSTDIR" "" "$TEMP\3DMonitor-uninstall-error.txt" "$AppLanguage"' $0
  ${If} ${Errors}
    MessageBox MB_ICONSTOP "$(UninstallCheckFailed)" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${If} $0 != 0
    FileOpen $1 "$TEMP\3DMonitor-uninstall-error.txt" r
    FileReadUTF16LE $1 $2
    FileClose $1
    MessageBox MB_ICONSTOP "$2" /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
FunctionEnd
Section "Uninstall"
  SetOutPath "$TEMP"
  !include "payload-delete.nsh"
  Delete "$INSTDIR\_installer-test.flag"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$DESKTOP\$MenuName.lnk"
  Delete "$SMPROGRAMS\$MenuName\3DMonitor.lnk"
  Delete "$SMPROGRAMS\$MenuName\Описание программы.lnk"
  Delete "$SMPROGRAMS\$MenuName\About 3DMonitor.lnk"
  Delete "$SMPROGRAMS\$MenuName\Удалить 3DMonitor.lnk"
  Delete "$SMPROGRAMS\$MenuName\Uninstall 3DMonitor.lnk"
  RMDir "$SMPROGRAMS\$MenuName"
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $RunName
  ${If} $0 == '$\"$INSTDIR\3DMonitor.exe$\" --no-browser'
    ${OrIf} $0 == '$\"$INSTDIR\K1Monitor.exe$\" --no-browser'
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" $RunName
  ${EndIf}
  DeleteRegKey HKCU $UninstallKey
  DeleteRegKey HKCU $AppKey
  DetailPrint "$(RetainedData)"
SectionEnd
