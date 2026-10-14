import {t,tm,m,trMarkup,locale} from './i18n.js?v=0.103.3';
// Short original Russian descriptions; diagnostic codes/raw text remain available.
// K1 source: https://wiki.creality.com/en/k1-flagship-series/k1-series-general-documents/error-code-summary
// Runout: official Creality Print 7.2 materialStatus=1 -> key2839, also key116.
// Text errors: https://www.klipper3d.org/FAQ.html
const creality=new Map();
for(const [codes,label] of [
  [[100],t('Не удалось скопировать G-code')],[[101,500],t('AI обнаружил возможный дефект печати')],
  [[102],t('Слишком длинное имя файла')],[[103,503],t('AI: возможная ошибка первого слоя')],[[104],t('AI: посторонний предмет на столе')],
  [[108,506,507],t('Ошибка калибровки лидара')],[[505],t('Лидар недоступен')],[[504],t('G-code не поддерживает проверку потока')],
  [[109,501],t('Неисправность вентилятора хотэнда')],[[502],t('Неисправность вентилятора платы')],[[508],t('Заканчивается память принтера')],
  [[110,2002,2343,2560],t('Нет связи с основной платой')],[[111,2344,2561,2506,3002],t('Нет связи с платой хотэнда')],[[112,2345,2562],t('Нет связи с платой стола')],
  [[116,2839],t('Закончился филамент')],[[2000,2298],t('Управляющая плата отключена')],[[2001],t('Сбой электроники принтера')],
  [[2014,2243],t('Движение за пределами области печати')],[[2016],t('Принтер не находится на паузе')],[[2019,2340],t('Температура вне допустимого диапазона')],
  [[2020,2021,2504],t('Ошибка поиска нулевой позиции')],[[2056,2065,2118,2119,2308,2313],t('Ошибка датчика ускорения')],
  [[2069],t('Неподдерживаемая команда вентилятора в G-code')],[[2090],t('Перегрузка управляющей платы')],[[2091],t('Сбой обмена с управляющей платой')],
  [[2095,2242],t('Не определена нулевая позиция')],[[2111,2210],t('Сопло слишком холодное для подачи филамента')],[[2112],t('Превышен допустимый объём экструзии')],
  [[2113,2114],t('Ошибка конфигурации экструдера')],[[2115,2116,2117,2513,2514],t('Некорректная команда или данные')],[[2211],t('Печать приостановлена')],
  [[2252,2253],t('Параметр вне допустимого диапазона')],[[2283],t('Принтер не готов')],[[2294],t('Принтер не ответил вовремя')],[[2295],t('Не удалось перезапустить плату')],
  [[2299,2300],t('Ошибка настройки управляющей платы')],[[2503,2520,2521,2522,2523,2524,2527,2529],t('Ошибка датчика выравнивания стола')],
  [[2505],t('Ошибка драйвера двигателя')],[[2509],t('Ошибка датчика температуры сопла')],[[2510],t('Ошибка датчика температуры стола')],[[2511],t('Ошибка температуры камеры')],[[2512],t('Ошибка температуры платы')],
  [[2526],t('Неравномерное движение оси Z')],[[2532],t('Сбой синхронизации управляющих плат')],[[2533],t('Данные выравнивания не соответствуют столу')],
  [[2563],t('Нет связи с контроллером принтера')],[[2564],t('Сопло не нагревается как ожидается')],[[2565],t('Стол не нагревается как ожидается')],
])for(const code of codes)creality.set(String(code),label);

const textPatterns=[
  [/filament (?:run(?:s? )?out|has run out)|out of filament|no filament|филамент закончился/i,t('Закончился филамент')],
  [/heater\s+['"]?extruder\w*['"]?\s+not heating|extruder\w*.*not heating at expected rate/i,t('Сопло не нагревается как ожидается')],
  [/heater\s+['"]?heater_bed['"]?\s+not heating|heater_bed.*not heating at expected rate/i,t('Стол не нагревается как ожидается')],
  [/lost communication with mcu|unable to connect to mcu|mcu.*(?:connection|connect).*timeout/i,t('Нет связи с управляющей платой')],
  [/adc out of range|mintemp|maxtemp|temperature.*out of range/i,t('Датчик температуры сообщил недопустимое значение')],
  [/move out of range/i,t('Движение за пределами области печати')],[/extrude below minimum temp/i,t('Сопло слишком холодное для подачи филамента')],
  [/must home axis first|must home.*first/i,t('Не определена нулевая позиция')],[/homing.*(?:timeout|failed)|no trigger on.*after full movement/i,t('Ошибка поиска нулевой позиции')],
  [/timer too close|rescheduled timer in the past/i,t('Сбой времени обмена с управляющей платой')],
  [/emergency stop|shutdown due to m112/i,t('Принтер остановлен аварийной командой')],[/thermal runaway/i,t('Нагрев вышел из допустимого режима')],
  [/offline after error/i,t('Принтер отключился после ошибки')],[/\battention\b/i,t('Принтер требует внимания')],
];
export function describePrinterError(item){
  const error=item.error??{code:item.error_code,key:null,message:''};
  const message=typeof t(error.message)==='string'?t(error.message).trim():'';
  const values=[error.key,error.code].filter(value=>value!=null&&!['','0','-1'].includes(String(value)));
  const codes=[...new Set(values.map(String))];
  const diagnostic=[codes.length?t('Код: ')+codes.join(' / '):'',message].filter(Boolean).join(' · ');
  const protocol=item.printer?.protocol??'creality';
  const model=String(item.printer?.model??'');
  // Codes from unrelated protocol/model families must never borrow a K1 meaning.
  const k1=protocol==='creality'&&(!model||/^K1(?:\b|C|MAX|SE|[_ -])/i.test(model)||model.toLowerCase()==='creality');
  if(k1)for(const value of codes){const match=/^(?:key[:_\s-]*)?(\d+)$/i.exec(value);const label=match&&creality.get(match[1]);if(label)return {summary:label,diagnostic};}
  if(protocol==='creality'&&codes.some(code=>/^(?:FR)?2839$/i.test(code)))return {summary:t('Закончился филамент'),diagnostic};
  for(const [pattern,label] of textPatterns)if(pattern.test(message))return {summary:label,diagnostic};
  if(item.filament_runout===true&&!message&&!codes.some(code=>!['116','2839'].includes(code)))return {summary:t('Закончился филамент'),diagnostic};
  if(/[а-яё]/i.test(message))return {summary:message.slice(0,240),diagnostic};
  return {summary:codes.length?t('Неизвестная ошибка принтера · код ')+codes.join(' / '):t('Принтер сообщил об ошибке'),diagnostic};
}
