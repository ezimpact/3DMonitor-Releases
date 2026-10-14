# 3DMonitor 0.103.3

## English

### New in 0.103.3 (since 0.102.1)

- **Job filament cost:** estimates the full planned G-code filament cost using a price per kg saved separately for each printer. The job keeps its original price and currency in print history when rates change.
- **Print cost calculator:** filament, average-power electricity estimate, printer hourly cost, labor hourly rate with separate labor hours/minutes, fixed manual work, additional materials, other expenses and markup.
- **Print failure reserve:** applied to filament, electricity and printer operation. Formula: printing cost × failure fraction / (1 − failure fraction). A 10% failure rate adds a 100 reserve to 900 of printing expenses. Labor, finishing and additional materials are excluded from this reserve.
- **RUB, USD and EUR:** default currency follows the application language (Russian → RUB, English → USD); an explicit selection is saved. Changing currency clears monetary inputs; there is no exchange-rate conversion.
- **Updated layout:** spool status and job filament cost are inside the current print card, below Pause/Stop/Exclude object. Nozzle monitoring is an icon inside the nozzle card, with a description after two seconds of hover or keyboard focus. Monitoring remains off by default.
- **Preserved settings:** printers, spool tracking, history, Telegram, remote pairing and selected monitoring preferences survive the update. No new background printer polling was added.

### Download

Windows x64: **3DMonitor-Setup-v0.103.3.exe** in Assets. The source archive and SHA256 files are also attached. Source code is available in this repository.

Validation: 347 Python tests on the clean public source, 108 JavaScript tests, 19 compiled checks and 75 installer TESTMODE checks. UI checked in Russian and English, including a 390px viewport, using fake printers. No real-printer control commands were sent.

## Русский

### Новое в 0.103.3 (после 0.102.1)

- **Стоимость пластика задания:** расчёт полного планового расхода G-code по цене за кг, заданной отдельно для каждого принтера. В истории сохраняются исходные цена и валюта задания, даже после изменения тарифов.
- **Калькулятор стоимости печати:** пластик, оценка электричества по средней мощности, стоимость часа принтера, почасовая оплата мастера с отдельным временем, фиксированная ручная работа, дополнительные материалы, прочие расходы и наценка.
- **Резерв брака:** учитывает повторную печать по формуле «расходы на печать × доля брака / (1 − доля брака)». При 10% брака расходы 900 ₽ дают резерв 100 ₽. Работа мастера, обработка и дополнительные материалы в резерв не входят.
- **Рубли, доллары и евро:** по умолчанию валюта зависит от языка (русский → RUB, английский → USD), ручной выбор сохраняется. При смене валюты цены очищаются; автоматической конвертации нет.
- **Обновлённый интерфейс:** катушка и стоимость пластика находятся в карточке текущей печати под кнопками «Пауза», «Стоп» и «Исключить модель». Контроль температуры — значок внутри карточки сопла, описание появляется через две секунды наведения или фокуса. По умолчанию контроль выключен.
- **Сохранение данных:** обновление сохраняет принтеры, катушки, историю, Telegram, удалённые привязки и выбранные настройки контроля. Дополнительного фонового опроса принтеров нет.

### Скачать

Для Windows x64 скачайте **3DMonitor-Setup-v0.103.3.exe** из Assets. Также приложены архив исходников и SHA256. Исходный код доступен в репозитории.

Проверки: 347 Python-тестов чистых исходников, 108 JavaScript-тестов, 19 проверок сборки и 75 проверок установщика TESTMODE. Интерфейс проверен на русском и английском, включая ширину390px, с макетами принтеров. Реальным принтерам команды управления не отправлялись.

## Screenshots / Скриншоты

![Printer dashboard / Панель принтеров](https://raw.githubusercontent.com/ezimpact/3DMonitor-Releases/main/dashboard-v0.103.3.png)

![Print cost calculator / Калькулятор стоимости](https://raw.githubusercontent.com/ezimpact/3DMonitor-Releases/main/cost-calculator-v0.103.3.png)


---

# 3DMonitor 0.102.1

## English

- **Filament spool tracking.** After confirmed filament runout and resuming the same print, 3DMonitor starts tracking a new spool. The default is **1000 g of filament**, configurable separately for each printer.
- **Remaining filament and low-supply warnings.** The dashboard shows estimated remaining weight and the filament needed to finish the job. Warnings use a **5% margin, at least 10 g**, and can be sent through configured Windows and Telegram notifications.
- **Works with uploads from different slicers.** G-code is read from the printer, including jobs uploaded through Creality Print or another application. Common text metadata from Creality Print, OrcaSlicer, Bambu Studio, PrusaSlicer, Cura and Simplify3D is supported, with extrusion analysis as a fallback. New large files may take time to analyse in the background.
- **Optional nozzle temperature monitoring.** Monitoring is **off by default** and can be enabled separately for each printer. Using existing telemetry every 5 seconds, it warns about sustained deviations of at least 10 °C, repeated sharp temperature jumps, or failure to reach an unchanged target. Heating and target changes are taken into account. Warnings suggest checking the nozzle assembly; they do not diagnose a specific fault or stop printing.
- **Settings survive updates.** Spool accounting and temperature-monitoring preferences are saved locally and preserved by the installer.

Existing spools initially have an **unknown remaining weight**. Enter a measured weight in **Spool settings**, or wait for the first runout and replacement. Estimates can change after purging or manual extrusion; unsupported G-code formats do not receive a guessed weight.

## Русский

- **Учёт катушек филамента.** После подтверждённого окончания пластика и продолжения той же печати 3DMonitor начинает отсчёт новой катушки. По умолчанию — **1000 г самого пластика**, с настройкой веса отдельно для каждого принтера.
- **Остаток пластика и предупреждения о нехватке.** В панели отображаются расчётный остаток и расход до конца задания. Предупреждения учитывают запас **5%, минимум 10 г** и могут приходить через настроенные уведомления Windows и Telegram.
- **Поддержка загрузок из разных слайсеров.** G-code читается с принтера, в том числе при загрузке через Creality Print или другое приложение. Поддержаны распространённые текстовые данные Creality Print, OrcaSlicer, Bambu Studio, PrusaSlicer, Cura и Simplify3D, а также запасной расчёт по командам экструзии. Анализ новых больших файлов выполняется в фоне и может занять время.
- **Включаемый контроль температуры сопла.** По умолчанию **выключен**, включается отдельно для каждого принтера. Раз в 5 секунд проверяет уже полученную телеметрию и предупреждает о длительном отклонении от 10 °C, повторных резких скачках или недостижении неизменной цели. Разогрев и смена цели учитываются. Предупреждение предлагает проверить узел сопла, не устанавливает конкретную причину неисправности и не останавливает печать.
- **Сохранение настроек при обновлении.** Учёт катушек и выбранный режим контроля температуры хранятся на ПК и сохраняются установщиком.

У уже установленных катушек остаток сначала **неизвестен**. Укажите измеренный вес в **«Настройках катушки»** или дождитесь первой замены после окончания пластика. Продувка и ручная подача могут изменить расчёт; для неподдерживаемых форматов G-code вес не угадывается.
