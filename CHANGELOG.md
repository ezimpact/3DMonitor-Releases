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
