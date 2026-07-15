# Сервис камеры Canon для Raspberry Pi (alpha)

Этот сервис относится к единой экспериментальной версии `v1.8.0-alpha`. Все актуальные изменения этапа интеграции камеры накапливаются в ней без отдельных `.1`, `.2` и т. п. Сервис управляет Canon через `gphoto2` и даёт основному приложению HTTP API для ручной проверки:

- инициализация и `gphoto2 --summary`;
- LiveView без VNC Viewer и `ffplay`;
- сохранение текущего preview-кадра;
- полноразмерная фотография;
- запись LiveView-потока в MP4;
- выбор доступного конкретной камерой JPEG-качества фотографии;
- раздельный выбор качества/размера LiveView и FPS, когда камера предоставляет эти параметры;
- скачивание фотографий и видео на основной компьютер с опциональным удалением исходника на Raspberry Pi.

Сервис пока не связан с ВАЯХ, Ossila, сериями или пикселями OLED.

## 1. Установка на Raspberry Pi

Проверить камеру и установить системные программы:

```bash
sudo apt update
sudo apt install -y gphoto2 ffmpeg python3-venv
gphoto2 --auto-detect
gphoto2 --summary
ffmpeg -version
ffprobe -version
```

Инструкция и примеры службы рассчитаны на Linux-пользователя `user`. Скопировать папку `raspberry_camera_service` на Raspberry Pi в `/home/user/oled-camera`, затем выполнить:

```bash
cd /home/user/oled-camera
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp config.example.json config.json
```

Если фактический Linux-пользователь отличается от `user`, изменить `User`, домашние пути службы и `data_dir` в `config.json` согласованно.

## 2. Первый ручной запуск

```bash
cd /home/user/oled-camera
.venv/bin/python camera_service.py --config config.json
```

С другого терминала Raspberry Pi:

```bash
curl http://127.0.0.1:8765/api/health
curl -X POST http://127.0.0.1:8765/api/camera/initialize
curl http://127.0.0.1:8765/api/camera/status
curl http://127.0.0.1:8765/api/camera/capabilities
```

После этого на основном компьютере открыть приложение, нажать `Камера (alpha)`, указать IP Raspberry Pi и порт `8765`, затем нажать `Подключиться`.

Если Raspberry Pi раздаёт хотспот, типичный адрес — `192.168.4.1`, но нужно использовать фактический адрес своей конфигурации.

## 3. Автозапуск через systemd

Отредактировать `oled-camera.service.example`, затем установить службу:

```bash
sudo cp oled-camera.service.example /etc/systemd/system/oled-camera.service
sudo systemctl daemon-reload
sudo systemctl enable --now oled-camera.service
sudo systemctl status oled-camera.service
```

Просмотр логов:

```bash
journalctl -u oled-camera.service -f
tail -f /home/user/oled-camera/camera_data/logs/camera_service.log
```

## 4. Как устроена запись

Для LiveView запускается один процесс:

```bash
gphoto2 --stdout --capture-movie
```

Сервис выделяет из MJPEG-потока отдельные JPEG-кадры. Эти же кадры одновременно:

1. отдаются приложению как `multipart/x-mixed-replace`;
2. при записи передаются в `ffmpeg` через `stdin`.

Поэтому при нажатии `Начать запись видео` LiveView не перезапускается. FFmpeg использует временные метки поступления кадров и режим VFR, без искусственно заданного FPS. Готовое видео кодируется в H.264 (`libx264`, `yuv420p`) без звука. Если Raspberry Pi не успевает кодировать, можно выбрать `superfast` или `ultrafast` в `config.json`.

При инициализации сервис ищет среди конфигурационных виджетов камеры настройки качества/размера LiveView и частоты кадров. В приложение передаются только варианты `Choice`, реально возвращённые этой камерой. Если отдельного управления FPS нет, поле FPS отключено, а FFmpeg сохраняет фактические временные метки потока в режиме VFR. Сжатие MP4 задаётся `ffmpeg_crf` в `config.json`.

Файлы сначала пишутся в `camera_data/temporary` с окончанием `.part`. После штатной остановки MP4 проверяется через `ffprobe` и только затем переносится в `camera_data/videos`. Повреждённые или незавершённые файлы попадают в `camera_data/failed`.

## 5. Фото и LiveView

Полноразмерное фото нельзя гарантированно снять одновременно с `--capture-movie`. Поэтому сервис временно останавливает LiveView, выполняет:

```bash
gphoto2 --filename <путь>.jpg --capture-image-and-download
```

и затем автоматически восстанавливает LiveView. Кнопка `Сохранить кадр LiveView` не останавливает поток: она сохраняет последний preview-кадр.

При инициализации сервис выполняет `gphoto2 --list-config` и читает подходящие `imageformat`, `imagequality`, `imagesize`, `resolution` или `quality`. Приложение показывает только варианты, реально возвращённые подключённой камерой. RAW и RAW+JPEG исключены на этом этапе: текущая операция фото ожидает один безопасно скачиваемый JPEG-файл.

Если флажок `Оставлять файл на Raspberry Pi после скачивания` снят, desktop-приложение сначала полностью скачивает `.part`, проверяет размер и SHA-256, атомарно переименовывает локальный файл и только затем вызывает удаление на Pi. При ошибке удаления проверенный локальный файл сохраняется.

## 6. Освобождение камеры от GVFS

При инициализации сервис сначала завершает только процессы, связанные с gPhoto2/GVFS. Если `gphoto2 --summary` сообщает, что USB-устройство занято, разрешён резервный `pkill -f gvfs`, соответствующий текущему ручному процессу. Его можно отключить настройкой:

```json
"allow_broad_gvfs_cleanup": false
```

Глобальный `pkill gphoto2` или `pkill ffmpeg` не используется: сервис завершает только запущенные им процессы.

## 7. API

Основные адреса:

```text
GET  /api/health
GET  /api/camera/status
POST /api/camera/initialize
GET  /api/camera/capabilities
POST /api/liveview/start             # JSON: video_settings по данным capabilities
GET  /api/liveview/stream
POST /api/liveview/stop
POST /api/liveview/snapshot
POST /api/photo/capture              # JSON: photo_settings по данным capabilities
POST /api/video/start                # JSON: video_settings по данным capabilities
POST /api/video/stop
GET  /api/files
GET  /api/files/{file_id}
DELETE /api/files/{file_id}
```

Swagger-документация доступна по адресу `http://<raspberry-pi>:8765/docs`.

## 8. Ограничения alpha

- Поток `gphoto2 --capture-movie` является preview/LiveView, а не обязательно внутренним видеорежимом Canon максимального качества.
- Фактические FPS, разрешение и стабильность зависят от модели камеры и USB-соединения.
- Если камера экспортирует качество и FPS одним объединённым режимом, приложение показывает его как режим качества и не создаёт несуществующий независимый выбор FPS.
- API не имеет авторизации и должен использоваться только в доверенной локальной сети/хотспоте.
- Автоматическая связь с измерениями OLED пока отсутствует.
- Перед научным использованием нужно проверить реальную Canon: 20 циклов LiveView → фото → LiveView → видео, 15 минут LiveView и видео максимальной ожидаемой длительности.
