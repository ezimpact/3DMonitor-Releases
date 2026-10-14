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


Run and build: [BUILD.md](../../BUILD.md).
