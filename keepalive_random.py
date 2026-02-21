#!/usr/bin/env python3
"""
keepalive_random.py

Периодически "пингует" указанный URL с рандомизированными интервалами и заголовками.
Используй с осторожностью — возможное нарушение правил хостинга.
"""

import asyncio
import aiohttp
import logging
import os
import random
import sys
import time
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

# ------------------------- КОНФИГ -------------------------
URLS = [
    "https://aimatrix-e8zs.onrender.com/upload_files_pyrus",
    # можно добавить дополнительные url'ы или эндпойнты /health, /
]

MIN_INTERVAL_SECONDS = 60 * 10    # минимальный интервал между пингами (сек) — например 10 минут
MAX_INTERVAL_SECONDS = 60 * 25    # максимальный (сек) — например 25 минут

# Дополнительный джиттер (±) в процентах от случайно выбранного интервала
JITTER_PCT = 0.15  # 15%

# Таймаут на HTTP-запрос (сек)
REQUEST_TIMEOUT = 30

# Максимум повторных попыток при ошибках перед увеличением backoff
MAX_RETRIES = 3

# Базовый экспоненциальный бэкофф (сек), умножается на 2^n
BACKOFF_BASE = 5

# Логи — файл (на сервере без root задайте KEEPALIVE_LOGFILE, например ./keepalive.log)
LOGFILE = os.environ.get("KEEPALIVE_LOGFILE", "/var/log/keepalive_random.log")

# User-Agents (пример набора, можно расширить)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko)",
    "curl/7.85.0",
    "Wget/1.21.3 (linux-gnu)",
]

# Доп. query-параметры, которые можно подставлять рандомно
EXTRA_QUERY_KEYS = ["t", "v", "rand", "token", "src"]
# -----------------------------------------------------------

# Настройка логирования
logger = logging.getLogger("keepalive")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

# Лог в stdout
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(formatter)
logger.addHandler(sh)

# Лог в файл (попробуем добавить handler, игнорируем ошибки открытия файла)
try:
    fh = logging.FileHandler(LOGFILE)
    fh.setFormatter(formatter)
    logger.addHandler(fh)
except Exception as e:
    logger.warning(f"Не удалось открыть файл лога {LOGFILE}: {e}. Лог будет только в stdout.")

def random_interval():
    """Вернуть интервал с учётом джиттера."""
    base = random.uniform(MIN_INTERVAL_SECONDS, MAX_INTERVAL_SECONDS)
    jitter = base * JITTER_PCT
    value = random.uniform(base - jitter, base + jitter)
    return max(1, value)

def random_headers():
    """Сгенерировать случайные заголовки (User-Agent + базовые)."""
    ua = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": ua,
        "Accept": "*/*",
        # можно добавить другие заголовки, но не слишком много
    }
    return headers

def randomize_url(url: str) -> str:
    """
    Добавляет случайные query-параметры к url, чтобы запросы выглядели по-разному.
    Не меняет исходный путь.
    """
    parsed = urlparse(url)
    q = dict(parse_qsl(parsed.query, keep_blank_values=True))
    # иногда не добавляем параметр (чтобы было разнообразие)
    if random.random() < 0.7:
        key = random.choice(EXTRA_QUERY_KEYS)
        q[key] = str(int(time.time())) + "_" + str(random.randint(1, 99999))
    new_query = urlencode(q)
    new = parsed._replace(query=new_query)
    return urlunparse(new)

async def do_request(session: aiohttp.ClientSession, url: str):
    """Выполнить один запрос с обработкой времени и логированием"""
    headers = random_headers()
    url2 = randomize_url(url)
    start = time.perf_counter()
    try:
        async with session.get(url2, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
            elapsed = time.perf_counter() - start
            text_snippet = await resp.text()
            # логируем статус и время; не печатаем весь текст ответа (можно ограничить)
            logger.info(f"URL: {url2} → {resp.status} time={elapsed:.2f}s len={len(text_snippet)}")
            return resp.status, elapsed
    except asyncio.TimeoutError:
        elapsed = time.perf_counter() - start
        logger.warning(f"Timeout after {elapsed:.2f}s for {url2}")
        raise
    except Exception as e:
        elapsed = time.perf_counter() - start
        logger.error(f"Request error for {url2}: {e} (took {elapsed:.2f}s)")
        raise

async def worker():
    """Бесконечный рабочий цикл: пингуем случайный URL, ждём рандомный интервал."""
    connector = aiohttp.TCPConnector(limit_per_host=4)
    async with aiohttp.ClientSession(connector=connector) as session:
        consecutive_errors = 0
        while True:
            url = random.choice(URLS)
            try:
                # Попробуем серию попыток с небольшим ретраем
                attempt = 0
                while True:
                    attempt += 1
                    try:
                        status, elapsed = await do_request(session, url)
                        # успех — сбрасываем счётчик ошибок
                        consecutive_errors = 0
                        break
                    except Exception:
                        if attempt >= MAX_RETRIES:
                            consecutive_errors += 1
                            # экспоненциальный бэкофф
                            backoff = BACKOFF_BASE * (2 ** (consecutive_errors - 1))
                            logger.warning(f"Max retries reached for {url}. Backoff {backoff}s (consecutive errors: {consecutive_errors})")
                            await asyncio.sleep(backoff)
                            break
                        else:
                            retry_wait = BACKOFF_BASE * (2 ** (attempt - 1))
                            logger.info(f"Retry {attempt}/{MAX_RETRIES} after {retry_wait}s")
                            await asyncio.sleep(retry_wait)
                # Вычисляем следующий случайный интервал
                interval = random_interval()
                logger.info(f"Следующий пинг через {interval/60:.2f} минут ({interval:.0f} сек)")
                await asyncio.sleep(interval)
            except Exception as e:
                # На верхнем уровне — при неожиданной ошибке, сделать небольшую паузу и продолжить
                logger.exception(f"Fatal loop error: {e}. Пауза 60 сек и продолжение.")
                await asyncio.sleep(60)

def main():
    logger.info("Запуск keepalive_random.py")
    try:
        asyncio.run(worker())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем (KeyboardInterrupt)")

if __name__ == "__main__":
    main()