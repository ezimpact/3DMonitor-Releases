# Run and build 3DMonitor 0.101

## English

This snapshot contains the application's Python code, browser interface, automated tests and Windows installer sources. It does not contain printer registrations, passwords, Telegram settings, remote pairings, print history or development backups.

### Run from source

Use Windows x64, Python 3.12 and Microsoft Edge. In PowerShell, from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r work\k1-monitor\requirements-dev.txt
Copy-Item work\k1-monitor\config.example.json work\k1-monitor\config.json
.\.venv\Scripts\python.exe work\k1-monitor\run_monitor.py --no-tray --config work\k1-monitor\config.json --port 8879
```

The example configuration has no printers. Open the local dashboard at `http://127.0.0.1:8879/`. Add printers manually if you want to use them. Keep development separate from an existing installation; never copy its personal configuration into this repository.

### Tests

Use Node.js for browser-module tests. Python tests use local fixtures and mocks; they do not require real printers.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s work\k1-monitor\tests
node --test work\k1-monitor\tests\*.mjs
```

### Windows executable and installer

Install NSIS 3.13 from its official distribution. The build script accepts the path to `makensis.exe`; it also requires the Windows .NET Framework C# compiler for the installer helper.

```powershell
.\.venv\Scripts\python.exe work\build_release.py --nsis 'C:\Program Files (x86)\NSIS\makensis.exe'
```

The script creates a new clean release directory, builds the executable with the supplied PyInstaller specification, prepares the separate model-preview files and invokes the supplied installer builder. It refuses to reuse an existing release directory. The installer is written to `outputs/Public/`. No program is launched and no printer is contacted during the build.

The published installer's SHA256 is `87717366e970b7798d780142662cf260641d25da8dfaf299f38430d65b055591`. A local rebuild is not guaranteed to produce identical bytes: toolchains, timestamps and packaging can differ. Availability of source code does not by itself prove that an executable was built from that source or that software has no vulnerabilities.

The preview dependencies are separate, replaceable files. Their versions, hashes, upstream source links and rebuilding instructions are in [THIRD_PARTY.md](THIRD_PARTY.md).

## Русский

В этой копии есть Python-код программы, веб-интерфейс, автоматические тесты и исходники установщика Windows. База принтеров, пароли, настройки Telegram, удалённые привязки, история печати и рабочие бэкапы не включены.

### Запуск из исходников

Нужны Windows x64, Python 3.12 и Microsoft Edge. Выполните команды первого раздела в PowerShell из корня репозитория. Пример конфигурации содержит пустой список принтеров; панель откроется на `http://127.0.0.1:8879/`.

Для проверки используйте отдельную конфигурацию и порт. Подключение настоящих принтеров выполняется вручную. Не копируйте личные настройки работающей программы в репозиторий.

### Тесты и сборка

Команды тестов приведены выше: Python unittest и Node.js. Тесты используют локальные макеты и не требуют настоящих принтеров.

Для сборки установщика нужен NSIS 3.13 и компилятор C# из Windows .NET Framework. `work/build_release.py` создаёт новый чистый комплект, собирает EXE, подготавливает отдельные файлы предпросмотра и запускает сборку установщика. Повторное использование существующей папки выпуска запрещено. Результат — `outputs/Public/`. Во время сборки программа не запускается, принтеры не опрашиваются.

Побайтовое совпадение самостоятельной сборки с опубликованным EXE не гарантируется. Открытый для просмотра код сам по себе не доказывает соответствие установщика этим исходникам или отсутствие уязвимостей.

Зависимости предпросмотра, их версии, хеши и исходники перечислены в [THIRD_PARTY.md](THIRD_PARTY.md).
