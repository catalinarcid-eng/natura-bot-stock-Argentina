from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import time
import requests
import os
import re
import json
from datetime import datetime, timedelta

# ─── Configuración ───────────────────────────────────────────────────────────
URL_ARGENTINA = "https://www.naturacosmeticos.com.ar/c/todos-productos"
WEBHOOK_URL = (
    "https://chat.googleapis.com/v1/spaces/AAQAljBv4Y4/messages"
    "?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI"
    "&token=YJVj-JROo0NRKnHP0QvIbve3aTVxE700D4wICDzAZb0"
)
MEMORIA_FILE = "memoria.json"
MEMORIA_TTL_DIAS = 7

# ─── Memoria ─────────────────────────────────────────────────────────────────

def cargar_memoria() -> dict:
    if not os.path.exists(MEMORIA_FILE):
        return {}
    try:
        with open(MEMORIA_FILE, "r", encoding="utf-8") as f:
            contenido = f.read().strip()
            if not contenido:
                return {}
            return json.loads(contenido)
    except Exception:
        return {}

def guardar_memoria(memoria: dict):
    with open(MEMORIA_FILE, "w", encoding="utf-8") as f:
        json.dump(memoria, f, ensure_ascii=False, indent=2)

def limpiar_viejos(memoria: dict) -> dict:
    corte = datetime.now() - timedelta(days=MEMORIA_TTL_DIAS)
    return {
        k: v for k, v in memoria.items()
        if datetime.fromisoformat(v["fecha"]) > corte
    }

def ya_notificado(memoria: dict, codigo: str) -> bool:
    return codigo in memoria

def marcar_notificado(memoria: dict, codigo: str, nombre: str):
    memoria[codigo] = {"nombre": nombre, "fecha": datetime.now().isoformat()}

# ─── Webhook ─────────────────────────────────────────────────────────────────

def enviar_webhook(productos: list):
    if not productos:
        return

    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    texto_intro = (
        f"ALERTA STOCK NATURA ARGENTINA [{now}]\n"
        f"Se encontraron {len(productos)} productos SIN STOCK nuevos:"
    )
    try:
        requests.post(WEBHOOK_URL, json={"text": texto_intro}, timeout=15)
        time.sleep(0.5)
    except Exception as e:
        print(f"error: {e}")

    BLOQUE = 15
    total_bloques = (len(productos) + BLOQUE - 1) // BLOQUE

    for i in range(0, len(productos), BLOQUE):
        bloque = productos[i:i + BLOQUE]
        num = (i // BLOQUE) + 1
        lineas = [f"Lista {num}/{total_bloques}:"]
        for p in bloque:
            lineas.append(f"{p['codigo']} | {p['nombre']}")
        payload = {"text": "\n".join(lineas)}
        try:
            r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
            r.raise_for_status()
            print(f"Bloque {num}/{total_bloques} enviado.")
        except Exception as e:
            print(f"Error bloque {num}: {e}")
        time.sleep(1)

def enviar_resumen(total: int, nuevos: int):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    payload = {"text": (
        f"Chequeo Natura Argentina completado [{now}]\n"
        f"Productos revisados: {total}\n"
        f"Nuevos sin stock notificados: {nuevos}"
    )}
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"Error resumen: {e}")

# ─── Selenium ────────────────────────────────────────────────────────────────

def crear_driver():
    opts = Options()
    opts.add_argument("--headless")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    return webdriver.Chrome(options=opts)

def obtener_sku_desde_url(url: str) -> str:
    match = re.search(r'(NAT[A-Z]+-\d+)', url, re.IGNORECASE)
    if match:
        return f"cod. {match.group(1).upper()}"
    return "cod. No detectado"

def obtener_sku_desde_pagina(driver, url: str) -> str:
    try:
        driver.get(url)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        for p in soup.find_all("p"):
            texto = p.get_text(strip=True)
            if texto.lower().startswith("cod."):
                return texto
        texto_pagina = soup.get_text(separator=" ")
        match = re.search(r'(cod\.\s*NAT[A-Z]+-\d+)', texto_pagina, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    except Exception as e:
        print(f"  Error obteniendo SKU de {url}: {e}")
    return "cod. No detectado"

def escanear_argentina(driver) -> list:
    print(f"Cargando {URL_ARGENTINA} ...")
    driver.get(URL_ARGENTINA)
    time.sleep(8)

    clics = 0
    while clics < 100:
        try:
            boton = driver.find_element(By.CSS_SELECTOR, '[data-testid="product-list-load-more"]')
            driver.execute_script("arguments[0].click();", boton)
            clics += 1
            print(f"  → Clic {clics} en 'explorar más resultados'...")
            time.sleep(3)
        except:
            break

    print(f"Todos los productos cargados ({clics} clics).")
    soup = BeautifulSoup(driver.page_source, "html.parser")
    sin_stock = []

    for h4 in soup.find_all("h4"):
        texto_h4 = h4.get_text(strip=True).lower()
        if "producto agotado" not in texto_h4:
            continue
        try:
            card = h4.parent
            a_tag = None
            for _ in range(8):
                if card:
                    a_tag = card.find("a", href=lambda x: x and "/p/" in x)
                    if a_tag:
                        break
                    card = card.parent

            if not a_tag:
                continue

            href = a_tag.get("href", "")
            if not href.startswith("http"):
                href = "https://www.naturacosmeticos.com.ar" + href

            nombre = ""
            if a_tag.get("aria-label"):
                nombre = a_tag["aria-label"].strip()
            if not nombre:
                for h4_inner in a_tag.find_all("h4"):
                    t = h4_inner.get_text(strip=True)
                    if "producto agotado" not in t.lower() and t:
                        nombre = t
                        break
            if not nombre:
                nombre = a_tag.get_text(separator=" ", strip=True)
                nombre = re.sub(r'producto agotado', '', nombre, flags=re.IGNORECASE).strip()
            if not nombre:
                nombre = "Producto sin nombre"

            codigo = obtener_sku_desde_url(href)
            sin_stock.append({
                "nombre": nombre,
                "codigo": codigo,
                "url": href,
                "necesita_visita": codigo == "cod. No detectado",
            })

        except Exception as e:
            print(f"Error procesando producto: {e}")

    print(f"📦 Productos sin stock encontrados: {len(sin_stock)}")
    return sin_stock

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"Bot Stock- {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    memoria = cargar_memoria()
    memoria = limpiar_viejos(memoria)

    driver = crear_driver()

    try:
        sin_stock = escanear_argentina(driver)
        nuevos_sin_stock = []

        print(f"\n🔍 Verificando memoria para {len(sin_stock)} productos sin stock...")
        for producto in sin_stock:
            if producto["necesita_visita"]:
                print(f"  → Visitando ficha: {producto['nombre']}")
                producto["codigo"] = obtener_sku_desde_pagina(driver, producto["url"])

            codigo = producto["codigo"]
            nombre = producto["nombre"]
            print(f"  • {nombre} | {codigo}")

            if ya_notificado(memoria, codigo):
                print(f"    ⏭️  Ya notificado, se omite.")
                continue

            marcar_notificado(memoria, codigo, nombre)
            nuevos_sin_stock.append(producto)

    finally:
        driver.quit()

    guardar_memoria(memoria)

    print(f"\n Nuevos sin stock: {len(nuevos_sin_stock)}")
    if nuevos_sin_stock:
        enviar_webhook(nuevos_sin_stock)

    enviar_resumen(total=len(sin_stock), nuevos=len(nuevos_sin_stock))
    print("fin.")


if __name__ == "__main__":
    main()
