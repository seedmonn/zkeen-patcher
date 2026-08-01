# Дизайн: автоматическое обновление геофайлов на всех точках

- **Дата:** 2026-08-01
- **Статус:** согласован в brainstorm
- **Связано:** `DEBUGGING.md`, `README.md`, релизы `geoip.dat` / `geosite.dat`

## 1. Цель

Один Python-скрипт, запускаемый с Mac (в LAN роутера), который идемпотентно и
безопасно обновляет `geoip.dat` / `geosite.dat` (на целях — `ip.dat` / `geo.dat`)
из последнего релиза `zkeen-patcher` сразу на **5 точках** и перезагружает их,
чтобы ядро Xray подхватило новые данные. Обновление проверяется по SHA256
(«золотой» эталон), при сбое — откат.

## 2. Точки (5)

| # | name | kind | SSH | Гео-папка | Рестарт |
|---|---|---|---|---|---|
| 1 | `MSK` | `xui` | `root@<IP>:22` (ключ) | `/usr/local/x-ui/bin` | API `restartXrayService` |
| 2 | `SPB` | `xui` | `seedmon@<IP>:53908` (ключ) | `/usr/local/x-ui/bin` | API `restartXrayService` |
| 3 | `EST` | `xui` | `seedmon@<IP>:53908` (ключ) | `/usr/local/x-ui/bin` | API `restartXrayService` |
| 4 | `ROUTER` | `router` | `root@192.168.1.1:22` (пароль) | `/opt/etc/xray/dat` | `xkeen -restart` |
| 5 | `LAN-MIRROR` | `docker-updater` | `ginseng@192.168.1.101:20202` (ключ) | (volume `geo-data:/data`) | `docker restart geo-updater` |

**Маппинг файлов** (везде): `geoip.dat → ip.dat`, `geosite.dat → geo.dat`.

**Секреты per-target** (токены/basePath/пароли) лежат в `~/.config/zkeen-patcher/targets.json`
(права `0600`, **вне репо**). В коммит идёт только `scripts/targets.example.json`
с плейсхолдерами. Соответствие `name → (panel.base, panel.token)`:

| name | panel.base | токен-файл |
|---|---|---|
| MSK | `https://<IP>:31441/<basePath_MSK>` | `<IP>.json` |
| SPB | `https://<PANEL_HOST>:8443/<basePath_SPB>` | `<IP>.json` |
| EST | `https://<PANEL_HOST>:8443/<basePath_EST>` | `<IP>.json` |

`<basePath_*>` и токены — секреты (в `targets.json`). IP/домены оставлены как в
`DEBUGGING.md` (не считаются секретом в рамках проекта).

### 2.1 Дополнительно по точкам

- **MSK/SPB/EST (xui):** SPB/EST — пользователь `seedmon`, для записи в
  `/usr/local/x-ui/bin` нужен sudo. MSK — `root`, sudo не нужен.
- **ROUTER:** `xkeen` на Entware/OpenWrt; доступ по паролю.
- **LAN-MIRROR:** контейнер `geo-updater` (`/home/ginseng/vless_routing_and_geo`)
  — это HTTP-зеркало геофайлов (порт `33133`). `app.py` сам качает из того же
  релиза zkeen-patcher каждые 2 ч и сразу при старте. Поэтому «обновление» здесь =
  `docker restart geo-updater` (немедленный рефетч) + проверка зеркала. **xray-msk/spb/est
  на этом хосте НЕ трогаем** (подтверждено). `ginseng` ∈ группа `docker` → sudo не нужен.

## 3. Источник и preflight (общий)

URL (один для всех):
- `https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geoip.dat`
- `https://github.com/seedmonn/zkeen-patcher/releases/latest/download/geosite.dat`

**Preflight на Mac** (до любых изменений на целях):
1. Скачать оба файла **один раз** в локальный кэш (`/tmp/zkeen-geo-*/`).
2. Проверить: HTTP 200 и размер ≥ `MIN_SIZE = 10240` байт (флор от ошибочной
   HTML-страницы/пустышки; реальный размер geoip ≈152 KB, geosite ≈80 KB).
3. Посчитать `SHA256` обоих → `GOLDEN = {ip: <sha>, geo: <sha>}`. Это эталон для
   всех точек.
4. `--dry-run` заканчивается здесь + печатает план по точкам.

## 4. Механика рестарта Xray (API) — ключевое

**Эндпоинт:** `POST <panel.base>/panel/api/server/restartXrayService`
- заголовок `Authorization: Bearer <token>`, TLS без проверки (`verify=False`),
  пустое тело.
- Ответ: `{"success": true, "msg": "...", "obj": null}`.
- Делает **полный рестарт процесса xray-core** (`Stop + NewProcess + Start`) →
  ядро перечитывает `geoip.dat`/`geosite.dat` с диска. Рестартит **только xray-core**
  (не панель, не ОС). Проверено по исходникам MHSanaei/3x-ui и alireza0/x-ui
  (high confidence).

**Фолбэки:**
- `404` → повторить на `/xui/API/server/restartXrayService` (префикс форка alireza0).
- `403` (CSRF) → читать сессионную cookie (логин через `/login` с `useCache` /
  методы панели) — реализуется как опция `panel.cookie` в конфиге, если понадобится.

**Важно (опровергает `DEBUGGING.md`, шаг D):** `POST /panel/api/xray/update`
гео-данные **не** перезагружает (он зовёт `RestartXray(false)` → gRPC hot-apply без
перезагрузки `.dat`, и no-op если конфиг не изменился). Поэтому используем именно
`restartXrayService`. Xray-core не имеет file-watcher'а; 3x-ui не следит за папкой
`bin` — замена `.dat` без рестарта игнорируется.

## 5. Алгоритмы по типам точек

### 5.1 `xui` (MSK/SPB/EST)
1. SSH (paramiko, ключ из агента, порт из конфига).
2. Для каждого файла `(ip.dat, geoip.dat)`, `(geo.dat, geosite.dat)`:
   0. Если `sha256sum <dir>/<name>` уже == `GOLDEN` (и не `--force`) — пропустить файл.
   1. SFTP-закачать локальный золотой файл в `/tmp/.zkeen.<name>.<rnd>` (в `/tmp` — без sudo).
   2. На боксе `sha256sum` → сравнить с `GOLDEN`. Несовпадение → abort точки (не трогаем боевой файл).
   3. Backup: `cp <dir>/<name> <dir>/<name>.bak` (sudo для seedmon; root — напрямую).
   4. Применить: `mv /tmp/.zkeen.<name>.<rnd> <dir>/<name> && chmod 644 && chown root:root` (sudo для seedmon).
   5. Проверить `sha256sum <dir>/<name>` == `GOLDEN`.
   - **Sudo-режим (seedmon):** сначала `sudo -n true`; если вышло — обычный `sudo`;
     иначе `sudo -S`, пароль `<SUDO_PASSWORD>` подаётся на stdin канала (в `ps` не светится).
3. Рестарт: `POST <base>/panel/api/server/restartXrayService` (+ фолбэки из §4).
4. **Post-check:** `sleep 5` → `pgrep -x xray`. Пусто (ядро не встало) →
   **rollback** (вернуть оба `.bak`, повторить рестарт), re-check, отметить ✗.

### 5.2 `router` (ROUTER)
1. SSH (paramiko, пароль).
2. Для каждого файла: SFTP в `/tmp/.zkeen.<name>.<rnd>`, `sha256sum` == `GOLDEN`,
   backup `.bak`, `mv` в `/opt/etc/xray/dat/<name>` (+ chmod/chown root), проверка SHA.
   - На Entware/OpenWrt `sha256sum` есть; если нет — фолбэк `openssl dgst -sha256`.
3. Рестарт: `xkeen -restart`.
4. **Post-check:** `sleep 5` → `pgrep -x xray`. Пусто → rollback + рестарт, ✗.

### 5.3 `docker-updater` (LAN-MIRROR)
1. SSH (paramiko, ключ; ginseng ∈ docker, без sudo).
2. `docker restart geo-updater` (по `container_name`).
3. **Post-check:** поллим `GET http://192.168.1.101:33133/{ip,geo}.dat` (с Mac,
   `requests`), каждые ~2 s до ~30 s; `SHA256` ответа == `GOLDEN` для обоих.
   Если релиз не изменился — зеркало уже совпадает, ✓ сразу. Таймаут → ✗ (доп.:
   `docker logs geo-updater --tail` через SSH для диагностики).

## 6. Безопасность

- **Атомарность:** пишем во временный файл, проверяем, потом `mv` (атомарный rename
  на той же ФС) — xray никогда не видит недописанный `.dat`.
- **Backup:** перед заменой — `<name>.bak`; откат при падении ядра на post-check.
- **Идемпотентность:** если SHA уже равен `GOLDEN` — пропускаем заливку/рестарт
  для точки (но для `docker-updater` рестарт всё равно делаем — он廉价 и форсирует рефетч).
  Уточнение: для `xui`/`router`, если оба файла уже `== GOLDEN`, можно пропустить
  точку целиком (по флагу `--force` игнорировать). По умолчанию — пропускаем.
- **Secrets:** токены/пароли только в `targets.json` (вне репо, `0600`). В логах
  токены редACTируются (`aaaaaaaa…`). `.gitignore` страхует `scripts/targets.json`
  на случай копирования туда.

## 7. CLI

```
python3 scripts/update_geofiles.py [опции]
```
- (без аргументов) — все точки.
- `--dry-run` — только preflight + печатать план, ничего не менять.
- `--only MSK,ROUTER` — подмножество точек.
- `--force` — применять даже если SHA уже равен эталону.
- `--no-restart` — только залить файлы, без рестарта (debug).
- `--config PATH` — путь к конфигу (по умолчанию `~/.config/zkeen-patcher/targets.json`).
- `-v` / `--verbose` — отладка SSH/API.
- **Exit code:** `0` если все ✓, `1` если хоть одна ✗. Точки отрабатывают независимо
  (сбой одной не валит остальные).

## 8. Структура вывода

Построчно по точкам: `✓ MSK  ip.dat/geo.dat updated, xray restarted (sha a1b2..)`.
Итоговая сводка в конце: `OK 5/5` или `FAIL 2/5: SPB, ROUTER`. Exit code см. §7.

## 9. Предварительные требования

- Python ≥ 3.10 (есть 3.10.4).
- `pip install --user paramiko requests` (`requests` есть; **paramiko нужно поставить**).
- ssh-agent с ed25519-ключом (для xui-root/seedmon и ginseng). Роутер — по паролю.
- Mac в LAN роутера (доступ до `192.168.1.1` и `192.168.1.101`).

## 10. Файлы в репо

- `scripts/update_geofiles.py` — скрипт.
- `scripts/targets.example.json` — шаблон конфига (плейсхолдеры).
- `scripts/README.md` — usage.
- `requirements.txt` — `paramiko`, `requests`.
- Корневой `README.md` — секция «Авто-обновление геофайлов на узлах» → `scripts/`.

## 11. Вне scope / follow-ups

- Поправить `DEBUGGING.md` (шаг D): для перезагрузки гео использовать
  `restartXrayService`, а не `xray/update`. Отдельный коммит.
- (Опц.) Harden preflight: проверять секции `.dat` локальными утилитами `inspect`/`inspectip`.
- (Опц.) Расписание на Mac (launchd/cron) для полностью автоматического ежедневного обновления.

## 12. Журнал решений (brainstorm)

- Рестарт VPS: через API `restartXrayService` (не SSH, не `xray/update`).
- Доступ на VPS: `seedmon`×2 + `root`; sudo авто (`sudo -n` → `sudo -S`, пароль из конфига).
- Запуск: с Mac в LAN роутера.
- Язык: Python3 + `paramiko` + `requests`.
- Хранение: скрипт в репо, секреты вне репо (`~/.config/zkeen-patcher/targets.json`).
- LAN-MIRROR: рестартим только `geo-updater` (xray-msk/spb/est не трогаем).
- Верификация: единая по SHA256 (золотой эталон из preflight).
