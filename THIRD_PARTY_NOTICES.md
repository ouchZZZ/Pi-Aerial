# Сторонние материалы / Third-party notices

MIT License относится только к исходному коду Pi Aerial.

## Apple Aerial

Видеоролики Apple, локализованные подписи и метаданные принадлежат Apple
или соответствующим правообладателям. Pi Aerial не является продуктом Apple
и не одобрен ею. Видео Apple не включены в исходники и релизы.

Загрузка и перекодирование на локальном устройстве технически поддерживаются,
но это не означает предоставления права на использование или распространение.
Пользователь самостоятельно проверяет применимые условия. Источник можно
отключить в панели управления.

Apple и Apple TV являются товарными знаками Apple Inc.

## NASA Image and Video Library

Возможны права третьих лиц и ограничения использования изображений людей
и символики NASA. Условия для каждой записи необходимо проверить.
https://www.nasa.gov/nasa-brand-center/images-and-media/

## Open-Meteo

Weather data by [Open-Meteo.com](https://open-meteo.com/).
Данные предоставляются по [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Приложение преобразует данные в текст, иконки и недельный прогноз.
Атрибуция находится на экране рядом с погодой. Бесплатный API рассчитан
на некоммерческое использование.
https://open-meteo.com/en/terms

Сторонние программные зависимости используются по собственным лицензиям.


## City search and OpenStreetMap map

City name search uses the Open-Meteo Geocoding API with geographic names
based on **GeoNames**: https://open-meteo.com/en/docs/geocoding-api

When an administrator explicitly opens the map, the interface loads
**Leaflet 1.9.4** (BSD-2-Clause license; https://leafletjs.com/)
and OpenStreetMap tiles. Map attribution is displayed inside the map:
© OpenStreetMap contributors (https://www.openstreetmap.org/copyright).
Tiles are requested only for the visible interactive viewport, without
prefetching or offline bulk downloads.

Clicking the map sends the chosen coordinates to the public
**OpenStreetMap Nominatim** reverse-geocoding service to find a nearby
settlement. Requests are user-initiated and rate-limited in the interface.
Source and usage policies:
- https://operations.osmfoundation.org/policies/tiles/
- https://operations.osmfoundation.org/policies/nominatim/
- https://www.openstreetmap.org/copyright

Open-Meteo may also be asked for the time zone at the selected point.
If external services are unavailable, the user can enter the city,
coordinates and time zone manually.
