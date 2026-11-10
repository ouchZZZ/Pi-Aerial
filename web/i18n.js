/* Pi Aerial dictionary-based UI localization. Extend translations with new locales. */
(function(global){
  'use strict';
  const enPairs=[
    // Static admin layout and forms
    ['Pi Aerial · Управление','Pi Aerial · Administration'],
    ['Панель управления','Control panel'],
    ['Подключение','Connecting'],
    ['Подключено','Connected'],
    ['Нет входа','Signed out'],
    ['Экран ↗','Display ↗'],
    ['Выйти','Sign out'],
    ['ЛОКАЛЬНАЯ СИСТЕМА','LOCAL SYSTEM'],
    ['Вход в панель','Sign in'],
    ['Управление видеозаставкой Raspberry Pi. Доступ только из доверенной сети.','Manage the Raspberry Pi video screensaver. Access from trusted networks only.'],
    ['Пароль администратора','Administrator password'],
    ['Войти в Pi Aerial','Sign in to Pi Aerial'],
    ['Ваша заставка, ваши правила.','Your display, your rules.'],
    ['Красивые видеопролёты, часы и погода. Всё хранится на малинке.','Scenic aerial videos, clock and weather. Everything lives on your Raspberry Pi.'],
    ['Видеозаписей','Videos'],
    ['Локальная коллекция','Local collection'],
    ['Свободно на карте','Free space'],
    ['Объём хранилища','Storage capacity'],
    ['Установлено модулей','Installed modules'],
    ['Изолированные виджеты','Isolated widgets'],
    ['Настройки','Settings'],
    ['Видео','Videos'],
    ['Готовые файлы','Ready files'],
    ['Модули','Modules'],
    ['Обновления','Updates'],
    ['Экран и часы','Display and clock'],
    ['Настройки применяются без перезагрузки системы.','Settings apply without restarting the device.'],
    ['Часы и погода','Clock and weather'],
    ['Показывать часы','Show clock'],
    ['Показывать дату и календарь','Show date and calendar'],
    ['Показывать погоду','Show weather'],
    ['Формат времени','Time format'],
    ['24 часа','24-hour'],
    ['12 часов','12-hour'],
    ['Погода и часовой пояс','Weather and time zone'],
    ['Город','City'],
    ['Широта','Latitude'],
    ['Долгота','Longitude'],
    ['Часовой пояс (IANA)','Time zone (IANA)'],
    ['Погода обновляется из Open-Meteo примерно каждые 25 минут, ключ API не требуется для некоммерческого использования.','Weather is refreshed from Open-Meteo approximately every 25 minutes. No API key is required for non-commercial use.'],
    ['Видеозаставка','Video screensaver'],
    ['Порядок','Playback order'],
    ['Случайный, без немедленных повторов','Shuffle, no immediate repeats'],
    ['По порядку','In order'],
    ['Смена ролика','Change video'],
    ['После окончания','When video ends'],
    ['Каждые 5 минут','Every 5 minutes'],
    ['Каждые 10 минут','Every 10 minutes'],
    ['Каждые 15 минут','Every 15 minutes'],
    ['Каждые 30 минут','Every 30 minutes'],
    ['Для Raspberry Pi 4 начните с 1080p H.264 MP4. 4K в Chromium требует проверки на конкретной сборке.','For Raspberry Pi 4, start with 1080p H.264 MP4. 4K playback in Chromium may vary by device.'],
    ['Ночной режим','Night mode'],
    ['Затемнять изображение ночью','Dim image at night'],
    ['Начало, час','Start hour'],
    ['Конец, час','End hour'],
    ['Оставшаяся яркость, %','Remaining brightness, %'],
    ['Сохранить настройки','Save settings'],
    ['Обновить экран','Refresh display'],
    ['Видеотека','Video library'],
    ['Онлайн-видео из выбранных источников, а просмотренные ролики остаются в ограниченном кеше на microSD.','Online videos from enabled sources. Played videos remain in a size-limited cache on the storage card.'],
    ['Загрузка и обработка видео','Downloading and processing'],
    ['Прогресс обновляется каждые 2 секунды. Для источников без известного размера или длительности процент не отображается, но объём, скорость и этап работы остаются видны.','Progress refreshes every 2 seconds. If size or duration is unknown, bytes, speed and processing stage are shown instead of a percentage.'],
    ['Получаю сведения о загрузках…','Loading transfer details…'],
    ['Источники','Sources'],
    ['Автозагрузка онлайн-роликов','Automatically download online videos'],
    ['При выключении текущий ролик докачается и перекодируется. Готовые ролики останутся доступны для показа.','When disabled, the current download and conversion finish. Prepared videos remain available.'],
    ['Включайте и выключайте источники независимо. «Загруженные файлы» управляют только участием собственных видео в воспроизведении и не затрагивают онлайн-кеш. Apple Aerial (tvOS 26) и NASA не требуют ключей. Apple скачивается строго по одному видео: 1080p в приоритете, затем перекодирование в H.264 MP4.','Enable sources independently. Uploaded files control only your own videos, not the online cache. Apple Aerial (tvOS 26) and NASA need no API keys. Apple videos are processed one at a time, prioritizing 1080p and converting to H.264 MP4.'],
    ['Повторить проверку источников','Refresh source catalogs'],
    ['Кеш подготовленных видео','Prepared video cache'],
    ['Готовить и хранить онлайн-ролики на карте памяти','Prepare and store online videos locally'],
    ['Максимальный размер кеша (ГБ)','Maximum cache size (GB)'],
    ['Проверяю заполнение кеша…','Checking cache usage…'],
    ['Сохранить','Save'],
    ['Очистить кеш','Clear cache'],
    ['Достигли лимита кеша или резерва свободного места (5 ГБ) — новые загрузки автоматически ставятся на паузу, уже готовые MP4 не удаляются. После увеличения лимита или освобождения места скачивание продолжится. Исходник удаляется после подготовки; история уже обработанных ID сохраняется.','When the cache limit or free-space reserve (5 GB) is reached, new downloads pause without deleting ready MP4 files. Downloads resume when space is available. Originals are removed after conversion; processed IDs are retained.'],
    ['Загрузить ролик','Upload a video'],
    ['Загрузить на малинку','Upload to Raspberry Pi'],
    ['MP4 / WebM / M4V / MOV / MKV. После загрузки видео автоматически готовится в фоне: H.264, MP4, до 1080p/30 кадров/с. Совместимый H.264 перепаковывается без потери качества. Исходный файл сохраняется до успешного завершения; статус и повторная попытка доступны ниже.','MP4 / WebM / M4V / MOV / MKV. Uploaded videos are prepared in the background as H.264 MP4 at up to 1080p/30 fps. Compatible H.264 streams are remuxed without quality loss. Originals are retained until conversion succeeds.'],
    ['Загруженные файлы','Uploaded files'],
    ['Готовые видео','Prepared videos'],
    ['Все сконвертированные файлы и история роликов, которые уже скачивали.','All converted videos and previously downloaded item history.'],
    ['Остановить разовый показ','Stop one-time playback'],
    ['Онлайн-ролики Apple и NASA','Apple and NASA online videos'],
    ['Обновить список','Refresh list'],
    ['Получаю данные видеотеки…','Loading video library…'],
    ['Удаление готового MP4 не снимает запрет на повторное скачивание. Для перекачивания сначала удалите файл, затем отдельно нажмите «Разрешить повторную загрузку».','Deleting a prepared MP4 does not remove its re-download block. Delete the file, then explicitly allow it to be downloaded again.'],
    ['Загруженные через веб файлы','Files uploaded through the web interface'],
    ['Дополнительные виджеты работают в изолированных фреймах и не получают доступ к панели.','Additional widgets run in sandboxed frames without access to the admin panel.'],
    ['Каталог модулей GitHub','GitHub module catalog'],
    ['Модули из официального GitHub: публичный HTTPS или старый read-only SSH-ключ.','Modules from the official GitHub repository, via public HTTPS or a legacy read-only SSH key.'],
    ['Показать каталог','Browse catalog'],
    ['Установить или обновить модуль','Install or update a module'],
    ['Загрузить подписанный ZIP','Upload signed ZIP'],
    ['Принимаются только пакеты с действительной Ed25519-подписью от доверенного ключа. Пример модуля включён в архив проекта.','Only packages with a valid Ed25519 signature from a trusted key are accepted. A sample module is included in the repository.'],
    ['Установленные модули','Installed modules'],
    ['Обновления ядра','Core updates'],
    ['Установка из GitHub или подписанного ZIP, отдельный процесс и автоматический откат при сбое.','Updates from GitHub or a signed ZIP, with an isolated process and automatic rollback on failure.'],
    ['Из GitHub · main','From GitHub · main'],
    ['Проверить новую версию в ouchZZZ/Pi-Aerial и установить с автоматическим откатом, если служба не запустится.','Check ouchZZZ/Pi-Aerial for updates with automatic rollback if the service fails to start.'],
    ['Обновления из GitHub не имеют отдельной криптографической подписи: доверие основано на контроле аккаунта ouchZZZ и HTTPS. Подпись Ed25519 проверяется только у загруженных ZIP.','GitHub updates have no separate cryptographic signature: trust depends on control of the ouchZZZ account and HTTPS. Only uploaded ZIP packages are verified with Ed25519.'],
    ['Проверить GitHub','Check GitHub'],
    ['Установить обновление','Install update'],
    ['Проверка ещё не выполнялась','Not checked yet'],
    ['Установка новой версии','Installing a new version'],
    ['Версия загружается во временную папку. Отдельная служба проверяет запуск; при ошибке возвращает предыдущую рабочую версию. Не обновляйте сторонними ZIP.','The version is staged in a temporary directory and checked by a separate service. On failure, the previous release is restored. Do not install untrusted ZIP files.'],
    ['Безопасность обновлений','Update security'],
    ['Доверенные ключи','Trusted keys'],
    ['Публичные Ed25519 ключи на Raspberry Pi','Public Ed25519 keys on Raspberry Pi'],
    ['Статус','Status'],
    ['Состояние очереди обновления ядра','Core update queue state'],
    ['Нет обновлений','No updates'],
    ['Добавление доверенных ключей выполняется локально через терминал. Приватный ключ никогда не хранится на телевизоре.','Trusted keys are added locally using the terminal. Private keys must never be stored on the display.'],
    ['Просмотр видео','Preview video'],
    ['Закрыть','Close'],
    // New controls
    ['Язык интерфейса','Interface language'],
    ['Поиск города','Search for city'],
    ['Найти город','Find city'],
    ['Показать карту','Show map'],
    ['Скрыть карту','Hide map'],
    ['Нажмите на карту, чтобы выбрать координаты и ближайший город.','Click the map to select coordinates and the nearest city.'],
    ['OpenStreetMap и GeoNames через Open-Meteo; для карты нужен интернет.','OpenStreetMap and GeoNames via Open-Meteo; map requires an internet connection.'],
    ['Введите минимум 2 символа названия города.','Enter at least 2 characters of the city name.'],
    ['Города не найдены. Уточните запрос.','No matching cities. Refine your search.'],
    ['Не удалось загрузить карту. Введите город и координаты вручную.','Map failed to load. Enter the city and coordinates manually.'],
    ['Не удалось определить город. Введите название вручную.','Could not identify the city. Enter its name manually.'],
    ['Выберите город из результатов или точку на карте.','Choose a city from the results or a point on the map.'],
    ['Город выбран. Сохраните настройки.','City selected. Save settings.'],
    ['Ищу города…','Searching cities…'],
    ['Определяю ближайший населённый пункт…','Finding the nearest settlement…'],
    ['Не удалось загрузить список часовых поясов.','Could not load time zone list.'],
    ['Выбрать часовой пояс','Choose a time zone'],
    ['Место','Location'],
    ['Часовой пояс','Time zone'],
    ['Русский','Russian'],
    ['Английский','English'],
    // User-facing messages from FastAPI and conversion workers
    ['Неверный пароль','Invalid password'],
    ['Слишком много попыток. Повторите через пять минут.','Too many attempts. Try again in five minutes.'],
    ['Подготовленный ролик не найден','Prepared video not found'],
    ['Онлайн-ролик недоступен или источник выключен','Online video unavailable or source disabled'],
    ['MOV: ролик подготавливается в кеше MP4','Video is being converted to cached MP4'],
    ['Видеосервер вернул ошибку','Video server returned an error'],
    ['Не удалось подключиться к видеосерверу','Could not connect to the video server'],
    ['Блокировка повторной загрузки не найдена','Re-download block not found'],
    ['Файл превышает допустимый размер','File is too large'],
    ['Пустой файл','Empty file'],
    ['Поддерживаются MP4, WebM, M4V, MOV и MKV','Supported: MP4, WebM, M4V, MOV and MKV'],
    ['Нужно 9 ГБ свободного места для загрузки и обработки','9 GB of free space is required for upload and processing'],
    ['Это не MP4/M4V/MOV-файл','This is not an MP4/M4V/MOV file'],
    ['Это не WebM/MKV-файл','This is not a WebM/MKV file'],
    ['Видео принято и поставлено в очередь фоновой подготовки','Video accepted and queued for background processing'],
    ['Задание не найдено или не ожидает повторения','Job not found or not ready for retry'],
    ['Задание не найдено или выполняется','Job not found or currently running'],
    ['Предыдущее обновление ещё выполняется','The previous update is still running'],
    ['Обновление поставлено в очередь с откатом','Update queued with automatic rollback'],
    ['Установлена последняя версия','Latest version already installed'],
    ['Обновление из GitHub поставлено в очередь с откатом','GitHub update queued with automatic rollback'],
    ['Модуль установлен из GitHub','Module installed from GitHub'],
    ['Неизвестные параметры:','Unknown settings:'],
    ['Неверное название города','Invalid city name'],
    ['Неверные координаты','Invalid coordinates'],
    ['Неизвестный часовой пояс','Unknown time zone'],
    ['Неверные переключатели онлайн-источников','Invalid source selections'],
    ['Неверный режим кеширования','Invalid cache setting'],
    ['Лимит кеша должен быть от 1 до','Cache size must be between 1 and'],
    ['Неверное расположение часов','Invalid clock position'],
    ['Неверный формат часов','Invalid clock format'],
    ['Неверный режим видео','Invalid playback mode'],
    ['Неверный интервал видео','Invalid video interval'],
    ['Неверный час','Invalid hour'],
    ['Неверная яркость ночного режима','Invalid night dimming level'],
    ['GitHub не настроен: установите приложение из Git-клона через install.sh','GitHub not configured. Install from a Git clone using install.sh.'],
    ['Некорректный GitHub-репозиторий в конфигурации','Invalid GitHub repository configured'],
    ['SSH deploy key не настроен','SSH deploy key is not configured'],
    ['SSH deploy key не найден','SSH deploy key not found'],
    ['GitHub недоступен:','GitHub unavailable:'],
    ['Ошибка доступа к GitHub:','GitHub access failed:'],
    ['Не удалось получить SHA ветки main','Could not obtain the main branch SHA'],
    ['Ветка main изменилась после проверки; проверьте обновления снова','Main branch changed since the check. Check for updates again.'],
    ['Исходный файл больше не существует','Original file no longer exists'],
    ['Недостаточно места для конвертации: требуется резерв 5 ГБ','Insufficient space to convert: 5 GB reserve required'],
    ['Недостаточно места: нельзя нарушать резерв 5 ГБ','Insufficient space: 5 GB reserve must be maintained'],
    ['Исходный файл удалён до окончания обработки','Original file removed before processing finished'],
    ['В файле нет видеодорожки','No video stream in the file'],
    ['Не удалось определить размер видео','Could not determine video resolution'],
    ['ffmpeg превысил лимит обработки','ffmpeg processing timed out'],
    ['ffmpeg завершился с кодом','ffmpeg exited with code'],
    ['ffmpeg не установлен','ffmpeg is not installed'],
    ['ffmpeg не создал совместимый MP4','ffmpeg did not produce a compatible MP4'],
    ['Готовое видео пустое или слишком большое','Prepared video is empty or too large'],
    ['Готовый MP4 не подходит профилю Pi 4','Prepared MP4 is incompatible with Pi 4'],
    ['Каталог пуст или не содержит подходящих MP4','Catalog empty or has no suitable MP4 files'],
    ['Слишком большой ролик для кеша','Video exceeds cache size limit'],
    ['Достигнут лимит видео или свободного места','Video or free-space limit reached'],
    ['Пустой видеоролик','Empty video'],
    ['Ответ сервера не содержит MP4/MOV','Server response does not contain MP4/MOV'],
    ['Нужен ffmpeg для подготовки видео','ffmpeg is required for video preparation'],
    ['Недостаточно места для преобразования видео','Insufficient space to convert video'],
    ['Кеш заполнен во время подготовки нового видео','Cache filled while preparing a new video'],
    // Dynamic states, dialogs and notification phrases
    ['Объём ','Capacity '],
    ['Идёт установка','Installing'],
    [' КБ',' KB'],[' МБ',' MB'],[' ГБ',' GB'],
    ['неизвестно','unknown'],
    ['Соединение с сервером','Connecting to server'],
    ['Скачивание','Downloading'],
    ['Ожидание свободного конвертера','Waiting for converter'],
    ['Подготовка MP4','Preparing MP4'],
    ['Проверка готового файла','Final validation'],
    ['Сейчас загрузка и обработка не выполняются.','No active downloads or conversions.'],
    ['Загруженный файл','Uploaded file'],['Онлайн','Online'],
    [' из ',' of '],[' · размер неизвестен',' · unknown size'],['/с','/s'],
    ['перепаковка без потерь','lossless remux'],['H.264, перекодирование','H.264 transcoding'],
    [' · длительность неизвестна',' · unknown duration'],[' · прошло ',' · elapsed '],
    ['Недавние задания: ','Recent jobs: '],['Готово','Ready'],['Ошибка','Error'],
    [' · готов к воспроизведению',' · ready to play'],['Удалить','Delete'],
    ['Удалить готовое видео с карты памяти?','Delete the prepared video from storage?'],
    ['Видео удалено','Video deleted'],['В очереди','Queued'],['Фоновая конвертация','Background conversion'],
    ['Не удалось обработать','Processing failed'],['Удалить исходник','Delete original'],
    ['Удалить исходное видео с карты памяти?','Delete the original file from storage?'],
    ['Исходный файл удалён','Original file deleted'],['Загруженных файлов пока нет','No uploaded files yet'],
    ['Показывать загруженные через веб-интерфейс видео','Play videos uploaded through the web interface'],
    ['Локальные видео включены','Local videos enabled'],['Локальные видео отключены из показа','Local videos disabled'],
    [' Включён',' Enabled'],['Каталог готов','Catalog ready'],['Включить источник ','Enable source '],
    ['Источник обновлён. Каталог загрузится в фоне.','Source updated. The catalog will refresh in the background.'],
    ['Выключена','Off'],['Пауза: достигнут лимит кеша','Paused: cache limit reached'],
    ['Пауза: мало свободного места','Paused: low disk space'],
    ['Автозагрузка выключена','Auto-download disabled'],['Загрузка разрешена','Downloads enabled'],
    ['Не удалось получить список: ','Could not load list: '],
    ['Показать один раз без OSD','Play once without OSD'],['Запрос отправлен на экран заставки','Sent to the display'],
    ['Готов к просмотру · ','Ready to preview · '],
    ['MP4 удалён · повторная загрузка заблокирована','MP4 deleted · re-download blocked'],
    ['Просмотр','Preview'],['Удалить файл','Delete file'],
    ['Удалить готовый MP4? Запрет повторной загрузки останется.','Delete this MP4? The re-download block will remain.'],
    ['Файл удалён. Повторная загрузка по-прежнему заблокирована.','File deleted. Re-download is still blocked.'],
    ['Разрешить повторную загрузку','Allow re-download'],
    ['Снять блокировку и разрешить снова скачать этот ролик?','Remove block and allow this clip to download again?'],
    ['Блокировка снята. Загрузка начнётся по общей очереди.','Block removed. Download will use the normal queue.'],
    ['Ролика уже нет в текущем каталоге','Clip is no longer in the current catalog'],
    ['Пока нет скачанных роликов.','No downloaded videos yet.'],
    ['Локальная загрузка · ','Local upload · '],
    ['Удалить загруженный вами файл с карты?','Delete your uploaded file from storage?'],
    ['Файл удалён','File deleted'],['Своих готовых файлов пока нет.','No prepared local videos yet.'],
    ['Команда остановки отправлена','Stop command sent'],
    ['Модуль включён','Module enabled'],['Настройка сохранена','Setting saved'],
    ['Откатить','Roll back'],['Вернуть предыдущую версию модуля?','Restore the previous module version?'],
    ['Модуль откатан','Module rolled back'],['Обработка…','Processing…'],['Файл принят','File accepted'],
    ['Размер кеша: от 1 до ','Cache size: 1 to '],['Настройки кеша сохранены','Cache settings saved'],
    ['Удалить сохранённые копии онлайн-роликов? Свои видео останутся.','Delete cached online videos? Your uploads will remain.'],
    ['Удалено файлов кеша: ','Cached files deleted: '],
    ['Есть обновление','Update available'],['Актуальная версия','Up to date'],
    ['Установить новую версию из GitHub? При ошибке запуска будет откат.','Install the new GitHub version? It will roll back if startup fails.'],
    ['Загружаю каталог…','Loading catalog…'],['В GitHub пока нет модулей','No modules in GitHub yet'],
    ['нет','none'],['Переустановить','Reinstall'],['Установить / обновить','Install / update'],
    ['Не удалось обновить статус видео:','Could not refresh video status:'],
    ['Прогресс видео:','Video progress:'],['Список готовых видео:','Ready files list:'],
    ['Автозагрузка включена','Auto-download enabled'],
    [' · ранее загруженный файл',' · previously uploaded file'],
    ['Повторить','Retry'],['Файл снова в очереди','File re-queued'],['/с','/s'],
    ['Проверяю Apple и NASA…','Checking Apple and NASA…'],
    ['Проверка завершена','Check complete'],
    ['Каталоги Apple/NASA перепроверены','Apple/NASA catalogs refreshed'],
    ['Настройки применены','Settings applied'],
    ['Сохранено','Saved'],
    ['Экран перезагрузится при следующей проверке','Display will reload at the next check'],
    ['Готовые ролики сохраняются.','Prepared videos are retained.'],
    ['Использовано ','Used '],
    ['Установлено: ','Installed: '],
    ['Проверяю','Checking'],
    [' · установлено: ',' · installed: '],
    [' · готово к показу: ',' · playable: '],
    [' роликов',' clips'],
    [' готово',' ready'],
    [' в очереди/обработке',' queued/processing'],
    [' · не онлайн-кеш',' · not online cache']
  ];
  const dictionary=new Map(enPairs);
  const keys=[...dictionary.keys()].sort((a,b)=>b.length-a.length);
  const expr=new RegExp(keys.map(s=>s.replace(/[.*+?^$\\{}()|[\]]/g,'\\$&')).join('|'),'g');
  const remembered=new WeakMap();
  const attributes=new WeakMap();
  let language='ru',observer=null;
  function translate(value){
    const original=String(value);
    if(language!=='en')return original;
    return original.replace(expr,k=>dictionary.get(k)||k);
  }
  function translateText(node){
    let saved=remembered.get(node);
    const source=saved&&node.data===saved.rendered?saved.source:node.data;
    const newText=translate(source);
    remembered.set(node,{source,rendered:newText});
    if(node.data!==newText)node.data=newText;
  }
  function translateAttributes(element){
    if(!element.getAttribute)return;
    let saved=attributes.get(element)||{};
    for(const name of ['title','aria-label','placeholder']){
      const current=element.getAttribute(name);
      if(current===null)continue;
      const last=saved[name];
      const source=last&&current===last.rendered?last.source:current;
      const rendered=translate(source);
      saved[name]={source,rendered};
      if(current!==rendered)element.setAttribute(name,rendered);
    }
    attributes.set(element,saved);
  }
  function apply(root){
    if(!root)return;
    if(root.nodeType===3){translateText(root);return;}
    if(root.nodeType!==1&&root.nodeType!==9&&root.nodeType!==11)return;
    if(root.nodeType===1)translateAttributes(root);
    const walker=document.createTreeWalker(root,NodeFilter.SHOW_ELEMENT|NodeFilter.SHOW_TEXT);
    while(walker.nextNode()){
      const node=walker.currentNode;
      if(node.nodeType===3)translateText(node);
      else translateAttributes(node);
    }
  }
  function setLocale(lang){
    language=lang==='en'?'en':'ru';
    document.documentElement.lang=language;
    if(document.body)apply(document.body);
    document.title=language==='en'?'Pi Aerial · Administration':'Pi Aerial · Управление';
  }
  function observe(){
    if(observer||!document.body)return;
    observer=new MutationObserver(records=>{
      for(const record of records){
        if(record.type==='characterData')translateText(record.target);
        else if(record.type==='attributes')translateAttributes(record.target);
        else for(const node of record.addedNodes)apply(node);
      }
    });
    observer.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['title','aria-label','placeholder']});
    apply(document.body);
  }
  global.PiI18n={setLocale,observe,apply,translate,get language(){return language;}};
})(window);
