import json
import os
import time
import requests
from datetime import datetime, timedelta

# ─── Configuración ───────────────────────────────────────────────────────────
ACCOUNT = "naturacosmeticos"
ENVIRONMENT = "vtexcommercestable"
BASE_API = f"https://{ACCOUNT}.{ENVIRONMENT}.com.br"

# API VTEX Intelligent Search (pública, sin auth)
SEARCH_API = f"https://{ACCOUNT}.{ENVIRONMENT}.com.br/api/io/_v/api/intelligent-search/product_search/trade_policy/1"

# API VTEX legacy catalog (pública)
CATALOG_API = f"{BASE_API}/_v/public/products/search"

WEBHOOK_URL = (
    "https://chat.googleapis.com/v1/spaces/AAQAljBv4Y4/messages"
    "?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI"
    "&token=YJVj-JROo0NRKnHP0QvIbve3aTVxE700D4wICDzAZb0"
)
MEMORY_FILE = "memoria.json"
MEMORY_TTL_DAYS = 7

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "es-AR,es;q=0.9",
}

# ─── Memoria ─────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_memory(memory: dict):
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)

def clean_old_entries(memory: dict) -> dict:
    cutoff = datetime.now() - timedelta(days=MEMORY_TTL_DAYS)
    return {
        code: data for code, data in memory.items()
        if datetime.fromisoformat(data["fecha"]) > cutoff
    }

def is_already_notified(memory: dict, code: str) -> bool:
    return code in memory

def mark_as_notified(memory: dict, code: str, name: str):
    memory[code] = {"nombre": name, "fecha": datetime.now().isoformat()}

# ─── Webhook ─────────────────────────────────────────────────────────────────

def send_webhook(products: list):
    if not products:
        return
    lines = ["🚨 *Productos SIN STOCK en Natura Argentina:*\n"]
    for p in products:
        lines.append(f"• *{p['name']}*\n  Código: {p['code']}\n  🔗 {p['url']}")
    payload = {"text": "\n".join(lines)}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
        print(f"✅ Webhook enviado con {len(products)} producto(s).")
    except Exception as e:
        print(f"❌ Error webhook: {e}")

def send_heartbeat(total: int, new_oos: int):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    payload = {"text": (
        f"✅ *Chequeo Natura completado* [{now}]\n"
        f"Productos revisados: {total}\n"
        f"Nuevos sin stock notificados: {new_oos}"
    )}
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"❌ Error heartbeat: {e}")

# ─── API VTEX ────────────────────────────────────────────────────────────────

def fetch_all_products_vtex() -> list:
    """Obtiene todos los productos via API VTEX Catalog."""
    products = []
    page_size = 50
    page = 1

    print("📡 Consultando API VTEX...")
    while True:
        from_val = (page - 1) * page_size
        to_val = page * page_size - 1

        url = f"{BASE_API}/api/catalog_system/pub/products/search/"
        params = {
            "_from": from_val,
            "_to": to_val,
            "fq": "C:/",  # todas las categorías
        }

        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ⚠️  Error en página {page}: {e}")
            break

        if not data:
            print(f"✅ Fin de productos en página {page}.")
            break

        products.extend(data)
        print(f"  → Página {page}: {len(data)} productos (total: {len(products)})")

        if len(data) < page_size:
            break

        page += 1
        time.sleep(0.5)  # respetar rate limits

    return products


def fetch_products_by_category() -> list:
    """Alternativa: busca productos por categoría usando Intelligent Search."""
    products = []
    page = 0
    page_size = 50

    print("📡 Consultando API Intelligent Search...")
    while True:
        params = {
            "page": page + 1,
            "count": page_size,
            "query": "",
            "sort": "",
            "operator": "and",
            "fuzzy": "0",
            "leap": "false",
        }
        try:
            resp = requests.get(
                SEARCH_API,
                headers={**HEADERS, "x-vtex-language": "es-AR"},
                params=params,
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  ⚠️  Error en página {page+1}: {e}")
            break

        items = data.get("products", [])
        if not items:
            print(f"✅ Fin de productos en página {page+1}.")
            break

        products.extend(items)
        print(f"  → Página {page+1}: {len(items)} productos (total: {len(products)})")

        total = data.get("recordsFiltered", 0)
        if len(products) >= total or len(items) < page_size:
            break

        page += 1
        time.sleep(0.3)

    return products


def check_stock_catalog(product: dict) -> tuple:
    """
    Extrae nombre, código, URL y disponibilidad de un producto VTEX Catalog.
    Retorna (name, code, url, out_of_stock)
    """
    name = product.get("productName", "Sin nombre")
    ref = product.get("productReference", "")
    link = product.get("link", "")
    url = f"https://www.naturacosmeticos.com.ar{link}" if link.startswith("/") else link

    # Código: productReference o productId
    code = ref if ref else str(product.get("productId", "SIN-CODIGO"))

    # Stock: revisar SKUs
    out_of_stock = True
    items = product.get("items", [])
    for item in items:
        for seller in item.get("sellers", []):
            availability = seller.get("commertialOffer", {}).get("AvailableQuantity", 0)
            if availability and int(availability) > 0:
                out_of_stock = False
                break
        if not out_of_stock:
            break

    return name, code, url, out_of_stock


def check_stock_search(product: dict) -> tuple:
    """
    Extrae nombre, código, URL y disponibilidad de un producto Intelligent Search.
    """
    name = product.get("productName", "Sin nombre")
    link = product.get("link", "")
    url = f"https://www.naturacosmeticos.com.ar{link}" if link.startswith("/") else link

    # Código desde productReference o productId
    code = product.get("productReference", "") or str(product.get("productId", "SIN-CODIGO"))

    # Stock
    out_of_stock = True
    for item in product.get("items", []):
        for seller in item.get("sellers", []):
            qty = seller.get("commertialOffer", {}).get("AvailableQuantity", 0)
            if qty and int(qty) > 0:
                out_of_stock = False
                break

    return name, code, url, out_of_stock


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"🤖 Natura Stock Bot - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    memory = load_memory()
    memory = clean_old_entries(memory)
    new_out_of_stock = []

    # Intentar con API Catalog primero
    raw_products = fetch_all_products_vtex()

    # Si no funciona, intentar con Intelligent Search
    if not raw_products:
        print("⚠️  Catalog API sin resultados, intentando Intelligent Search...")
        raw_products = fetch_products_by_category()
        use_search_api = True
    else:
        use_search_api = False

    print(f"\n🔍 Analizando {len(raw_products)} productos...")

    for i, product in enumerate(raw_products, 1):
        if use_search_api:
            name, code, url, out_of_stock = check_stock_search(product)
        else:
            name, code, url, out_of_stock = check_stock_catalog(product)

        if not out_of_stock:
            continue

        print(f"  [{i}/{len(raw_products)}] SIN STOCK: {name} | {code}")

        if is_already_notified(memory, code):
            print(f"    ⏭️  Ya notificado, se omite.")
            continue

        mark_as_notified(memory, code, name)
        new_out_of_stock.append({"name": name, "code": code, "url": url})

    save_memory(memory)

    print(f"\n📣 Nuevos sin stock: {len(new_out_of_stock)}")
    if new_out_of_stock:
        send_webhook(new_out_of_stock)

    send_heartbeat(total=len(raw_products), new_oos=len(new_out_of_stock))
    print("✅ Bot finalizado.")


if __name__ == "__main__":
    main()
