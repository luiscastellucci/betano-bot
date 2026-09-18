"""
Monitor de mercado "Tiros al Arco" en Betano -> avisa por Telegram.

Cómo funciona:
- Abre cada partido de la lista MATCHES con un navegador real (Playwright).
- Entra a la pestaña "Jugadores" > "Estadísticas".
- Revisa si aparece el texto "Tiros al Arco".
- Si aparece y todavía no habíamos avisado de ese partido, manda un mensaje
  a tu Telegram y lo marca como "ya avisado" para no repetir el aviso.
- Repite cada CHECK_INTERVAL_SECONDS segundos, para todos los partidos.

IMPORTANTE:
- Betano tiene protección anti-bots. Este script usa un navegador real
  (no un simple request HTTP) para reducir el riesgo de bloqueo, pero
  no lo elimina. Si notás que empieza a fallar seguido o te aparecen
  captchas, hay que bajar la frecuencia (CHECK_INTERVAL_SECONDS) o
  agregar pausas más largas.
- Cuantos más partidos monitoreés a la vez y más rápido el intervalo,
  más probabilidad de que Betano lo detecte.
"""

import asyncio
import re
import time
import requests
from playwright.async_api import async_playwright

# ============ CONFIGURACIÓN ============

# Token del bot de Telegram (el que te dio BotFather)
TELEGRAM_TOKEN = "8912828456:AAFzG2PxBFWFbgreTTsgzpV7_4ja9UWbCcY"

# Tu chat_id de Telegram. Lo conseguís así:
# 1. Mandale cualquier mensaje a tu bot (ya lo hiciste: "Hola")
# 2. Andá a esta URL en el navegador (reemplazando TU_TOKEN):
#    https://api.telegram.org/botTU_TOKEN/getUpdates
# 3. Buscá el número "id" que aparece dentro de "chat": {"id": ...}
TELEGRAM_CHAT_ID = "6049265438"

# Si ponés esto en True, el script entra a LIGA_URL y arma la lista de
# partidos solo (no hace falta que le pases los links a mano).
# Si lo dejás en False, usa la lista fija MATCHES de más abajo.
DESCUBRIR_PARTIDOS_AUTOMATICAMENTE = False

# Página que lista todos los partidos de la Liga Profesional Argentina.
LIGA_URL = "https://caba.betano.bet.ar/sport/futbol/argentina/liga-profesional/195785/"

# Lista de partidos a monitorear (se usa solo si DESCUBRIR_PARTIDOS_AUTOMATICAMENTE
# está en False). Pegá acá el link de cada partido, con esta forma:
#   https://caba.betano.bet.ar/cuotas-de-partido/equipo1-equipo2/12345678/
MATCHES = [
    "https://caba.betano.bet.ar/cuotas-de-partido/river-plate-ca-huracan/89890619/",
    "https://caba.betano.bet.ar/cuotas-de-partido/central-cordoba-defensa-y-justicia/89890626/",
    "https://caba.betano.bet.ar/cuotas-de-partido/gimnasia-y-esgrima-la-plata-ca-banfield/89890614/",
    "https://caba.betano.bet.ar/cuotas-de-partido/union-de-santa-fe-ca-independiente/89890610/",
    "https://caba.betano.bet.ar/cuotas-de-partido/instituto-ac-cordoba-ca-talleres-de-cordoba/89890612/",
    "https://caba.betano.bet.ar/cuotas-de-partido/gimnasia-y-esgrima-mendoza-deportivo-riestra/89890629/",
    "https://caba.betano.bet.ar/cuotas-de-partido/san-lorenzo-boca-juniors/89890616/",
    "https://caba.betano.bet.ar/cuotas-de-partido/ca-rosario-central-argentinos-juniors/89890623/",
    "https://caba.betano.bet.ar/cuotas-de-partido/ca-platense-newells-old-boys/89890627/",
    "https://caba.betano.bet.ar/cuotas-de-partido/ca-belgrano-estudiantes-rio-cuarto/89890630/",
    "https://caba.betano.bet.ar/cuotas-de-partido/velez-sarsfield-ca-tigre/89890615/",
    "https://caba.betano.bet.ar/cuotas-de-partido/ca-aldosivi-club-atletico-tucuman/89890618/",
    "https://caba.betano.bet.ar/cuotas-de-partido/barracas-central-independiente-rivadavia/89890611/",
    "https://caba.betano.bet.ar/cuotas-de-partido/ca-lanus-estudiantes-de-la-plata/89890613/",
]

# Cada cuántos segundos revisa TODOS los partidos de la lista.
# Ojo: 10 segundos es agresivo, arrancá más arriba (30-60) para probar
# que no te bloqueen, y después lo bajamos si todo va bien.
CHECK_INTERVAL_SECONDS = 30

# Poné en True para que el navegador se abra visible (así podés ver qué
# está pasando). Ponelo en False de nuevo cuando lo dejes corriendo solo.
MODO_VISIBLE = False
# Si aparece CUALQUIERA de estos, se considera "disponible".
SEÑALES_MERCADO_ABIERTO = ["Tiros al Arco", "Tiros"]

# ========================================

# Guarda qué partidos ya avisamos, para no mandar el mensaje mil veces
ya_avisados = set()


def mandar_telegram(mensaje: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje}, timeout=10)
        if resp.status_code != 200:
            print(f"[ERROR] Telegram respondió {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[ERROR] No se pudo mandar el mensaje de Telegram: {e}")


async def cerrar_popups(page):
    """Cierra cartel de cookies y popups promocionales si aparecen."""
    rechazar_cookies = page.get_by_text("Rechazar todo", exact=True)
    if await rechazar_cookies.count() > 0 and await rechazar_cookies.first.is_visible():
        await rechazar_cookies.first.click()
        await page.wait_for_timeout(500)

    boton_cerrar = page.locator(
        "button[aria-label='Close'], button[aria-label='Cerrar'], "
        "[class*='close' i], [aria-label*='cerrar' i], [aria-label*='close' i]"
    )
    if await boton_cerrar.count() > 0 and await boton_cerrar.first.is_visible():
        await boton_cerrar.first.click()
        await page.wait_for_timeout(500)
    else:
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(500)


async def obtener_links_partidos(page) -> list:
    """Entra a la página de la liga y devuelve el link de cada partido."""
    try:
        await page.goto(LIGA_URL, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await cerrar_popups(page)
        await page.wait_for_timeout(1500)

        todos_los_hrefs = await page.eval_on_selector_all("a", "els => els.map(e => e.href)")
        partidos = sorted(set(h for h in todos_los_hrefs if "/cuotas-de-partido/" in h))
        return partidos

    except Exception as e:
        print(f"[ERROR] No se pudo obtener la lista de partidos: {e}")
        return []


def nombre_desde_url(url: str) -> str:
    """Arma un nombre legible tipo 'River Plate Ca Huracan' a partir del
    link, sin depender del título de la página (que a veces no carga)."""
    try:
        slug = url.rstrip("/").split("/cuotas-de-partido/")[1].split("/")[0]
        return slug.replace("-", " ").title()
    except Exception:
        return url


async def revisar_partido(page, url: str):
    """Devuelve (nombre_del_partido, lista de señales encontradas)."""
    encontradas = []
    nombre = nombre_desde_url(url)
    try:
        await page.goto(url, timeout=20000, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)  # deja que cargue contenido dinámico

        # Cerrar popups que puedan estar tapando el contenido
        await cerrar_popups(page)

        # Intentar hacer click en la pestaña "Jugadores" si existe
        jugadores_tab = page.get_by_text("Jugadores", exact=True)
        if await jugadores_tab.count() == 0:
            # Si ni siquiera existe la pestaña "Jugadores", el mercado seguro
            # no está disponible todavía.
            return nombre, encontradas

        await jugadores_tab.first.click()
        await page.wait_for_timeout(1000)

        # Dentro, click en "Estadísticas" si existe
        estadisticas_tab = page.get_by_text("Estadísticas", exact=True)
        if await estadisticas_tab.count() > 0:
            await estadisticas_tab.first.click()
            await page.wait_for_timeout(1000)

        # Para cada señal, nos fijamos que el texto esté REALMENTE VISIBLE
        # en pantalla (no solo presente en el HTML oculto de otra pestaña).
        # Esto evita, por ejemplo, confundir "Tiros" con "Tiros de esquina"
        # (que es un mercado distinto y puede estar siempre presente).
        for señal in SEÑALES_MERCADO_ABIERTO:
            locator = page.get_by_text(señal, exact=True)
            count = await locator.count()
            for i in range(count):
                elemento = locator.nth(i)
                if await elemento.is_visible():
                    encontradas.append(señal)
                    break

        return nombre, encontradas

    except Exception as e:
        print(f"[ERROR] Revisando {url}: {e}")
        return nombre, encontradas


# Cuántos partidos como máximo se revisan AL MISMO TIEMPO. Si el servidor
# donde corre tiene poca memoria (como los planes gratuitos), un número
# muy alto puede hacer que el navegador se quede sin memoria y se caiga
# ("Page crashed"). 3-4 es un buen equilibrio entre velocidad y estabilidad.
MAX_SIMULTANEOS = 2


async def revisar_uno(context, url: str, semaforo: asyncio.Semaphore):
    """Abre una pestaña propia para este partido y lo revisa."""
    async with semaforo:
        page = await context.new_page()
        try:
            nombre, señales_encontradas = await revisar_partido(page, url)
        finally:
            await page.close()

    estado = f"DISPONIBLE ({', '.join(señales_encontradas)})" if señales_encontradas else "todavía no"
    print(f"[{time.strftime('%H:%M:%S')}] {nombre} -> {estado}")

    if señales_encontradas:
        ya_avisados.add(url)
        mercados = " y ".join(señales_encontradas)
        mandar_telegram(f"🚨 {nombre}\n¡Ya está el mercado de {mercados}!\n{url}")


async def ciclo_de_revision(browser):
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    )
    # Bloqueamos imágenes, videos y fuentes: no las necesitamos para buscar
    # texto, y esto ahorra bastante memoria y ancho de banda.
    await context.route(
        re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|mp4|woff2?|ttf)(\?.*)?$", re.IGNORECASE),
        lambda route: route.abort(),
    )

    if DESCUBRIR_PARTIDOS_AUTOMATICAMENTE:
        page_liga = await context.new_page()
        partidos = await obtener_links_partidos(page_liga)
        await page_liga.close()
        print(f"[{time.strftime('%H:%M:%S')}] Encontrados {len(partidos)} partidos en la liga.")
    else:
        partidos = MATCHES

    # Revisamos varios partidos en simultáneo, pero limitado a
    # MAX_SIMULTANEOS a la vez, para no saturar la memoria del servidor.
    semaforo = asyncio.Semaphore(MAX_SIMULTANEOS)
    tareas = [revisar_uno(context, url, semaforo) for url in partidos if url not in ya_avisados]
    if tareas:
        await asyncio.gather(*tareas)

    await context.close()


async def main():
    print("Arrancó el monitor. Revisando cada", CHECK_INTERVAL_SECONDS, "segundos...")
    async with async_playwright() as p:
        # headless=True para que no abra ventana visible (necesario en servidor)
        browser = await p.chromium.launch(
            headless=not MODO_VISIBLE,
            args=[
                "--disable-dev-shm-usage",  # evita crashes por poca memoria compartida en Docker
                "--no-sandbox",
                "--disable-gpu",
            ],
        )

        while True:
            await ciclo_de_revision(browser)
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
