import json
import os
import time
import requests
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

# ─── Configuración ───────────────────────────────────────────────────────────
BASE_URL = "https://www.naturacosmeticos.com.ar/c/todos-productos"
WEBHOOK_URL = (
    "https://chat.googleapis.com/v1/spaces/AAQAljBv4Y4/messages"
    "?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI"
    "&token=YJVj-JROo0NRKnHP0QvIbve3aTVxE700D4wICDzAZb0"
)
MEMORY_FILE = "memoria.json"
MEMORY_TTL_DAYS = 7


# ─── Memoria ─────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    """Carga la memoria desde el archivo JSON."""
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_memory(memory: dict):
    """Guarda la memoria en el archivo JSON."""
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def clean_old_entries(memory: dict) -> dict:
    """Elimina entradas con más de 7 días."""
    cutoff = datetime.now() - timedelta(days=MEMORY_TTL_DAYS)
    cleaned = {
        code: data
        for code, data in memory.items()
        if datetime.fromisoformat(data["fecha"]) > cutoff
    }
    return cleaned


def is_already_notified(memory: dict, code: str) -> bool:
    return code in memory


def mark_as_notified(memory: dict, code: str, name: str):
    memory[code] = {
        "nombre": name,
        "fecha": datetime.now().isoformat(),
    }


# ─── Webhook ─────────────────────────────────────────────────────────────────

def send_webhook(products: list[dict]):
    """Envía mensaje a Google Chat con los productos sin stock."""
    if not products:
        return

    lines = ["🚨 *Productos SIN STOCK en Natura:*\n"]
    for p in products:
        lines.append(f"• *{p['name']}*\n  Código: `{p['code']}`\n  🔗 {p['url']}")

    message = "\n".join(lines)

    payload = {"text": message}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
        print(f"✅ Webhook enviado con {len(products)} producto(s).")
    except Exception as e:
        print(f"❌ Error al enviar webhook: {e}")


def send_heartbeat(total_checked: int, new_out_of_stock: int):
    """Envía un resumen al finalizar el chequeo."""
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    text = (
        f"✅ *Chequeo Natura completado* [{now}]\n"
        f"Productos revisados: {total_checked}\n"
        f"Nuevos sin stock notificados: {new_out_of_stock}"
    )
    payload = {"text": text}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Error al enviar heartbeat: {e}")


# ─── Scraping ────────────────────────────────────────────────────────────────

def get_product_code_from_page(page, url: str) -> str:
    """Visita la ficha de producto y extrae el código NATARG-XXX."""
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_selector("p.text-xs.text-low-emphasis", timeout=10000)
        elements = page.query_selector_all("p.text-xs.text-low-emphasis")
        for el in elements:
            text = el.inner_text().strip()
            if text.lower().startswith("cod."):
                return text.replace("cod.", "").strip()
    except Exception as e:
        print(f"  ⚠️  No se pudo obtener código de {url}: {e}")
    return "SIN-CODIGO"


def scrape_all_products(page) -> list[dict]:
    """Carga todos los productos presionando 'explorar más resultados'."""
    print(f"🌐 Cargando {BASE_URL} ...")
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    # Presionar el botón hasta que desaparezca
    clicks = 0
    while True:
        try:
            btn = page.locator("p.text-small.font-medium.lowercase", has_text="explorar más resultados")
            if btn.count() == 0:
                print(f"✅ Todos los productos cargados ({clicks} clics).")
                break
            btn.first.scroll_into_view_if_needed()
            btn.first.click()
            clicks += 1
            print(f"  → Clic {clicks} en 'explorar más resultados'...")
            time.sleep(2)
        except Exception as e:
            print(f"  ⚠️  No se encontró más el botón: {e}")
            break

    # Recolectar tarjetas de producto
    cards = page.query_selector_all("div.rounded-md.bg-white.cursor-pointer")
    print(f"📦 Productos encontrados en DOM: {len(cards)}")

    products = []
    for card in cards:
        try:
            # Nombre
            name_el = card.query_selector("h4.text-wrap")
            name = name_el.inner_text().strip() if name_el else "Sin nombre"

            # URL relativa → absoluta
            link_el = card.query_selector("a[href]")
            href = link_el.get_attribute("href") if link_el else ""
            url = f"https://www.naturacosmeticos.com.ar{href}" if href.startswith("/") else href

            # ¿Agotado?
            out_of_stock = card.query_selector("p.text-alert") is not None or \
                           "producto agotado" in (card.inner_text().lower())

            products.append({
                "name": name,
                "url": url,
                "out_of_stock": out_of_stock,
            })
        except Exception as e:
            print(f"  ⚠️  Error parseando tarjeta: {e}")

    return products


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"🤖 Natura Stock Bot - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    memory = load_memory()
    memory = clean_old_entries(memory)

    new_out_of_stock = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-http2",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "es-AR,es;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            locale="es-AR",
            viewport={"width": 1280, "height": 800},
        )
        # Página principal para scraping
        main_page = context.new_page()
        products = scrape_all_products(main_page)

        # Página secundaria para fichas de producto
        detail_page = context.new_page()

        print(f"\n🔍 Analizando {len(products)} productos...")
        for i, product in enumerate(products, 1):
            if not product["out_of_stock"]:
                continue

            print(f"  [{i}/{len(products)}] SIN STOCK: {product['name']}")

            # Obtener código desde la ficha
            code = get_product_code_from_page(detail_page, product["url"])
            print(f"    Código: {code}")

            if is_already_notified(memory, code):
                print(f"    ⏭️  Ya notificado, se omite.")
                continue

            mark_as_notified(memory, code, product["name"])
            new_out_of_stock.append({
                "name": product["name"],
                "code": code,
                "url": product["url"],
            })

        browser.close()

    save_memory(memory)

    print(f"\n📣 Nuevos sin stock para notificar: {len(new_out_of_stock)}")
    if new_out_of_stock:
        send_webhook(new_out_of_stock)

    send_heartbeat(
        total_checked=len(products),
        new_out_of_stock=len(new_out_of_stock),
    )
    print("✅ Bot finalizado.")


if __name__ == "__main__":
    main()

