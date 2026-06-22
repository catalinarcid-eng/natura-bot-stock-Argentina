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

# Webhook de Google Chat (resumen corto)
WEBHOOK_CHAT_URL = (
    "https://chat.googleapis.com/v1/spaces/AAQAljBv4Y4/messages"
    "?key=AIzaSyDdI0hCZtE6vySjMm-WEfRq3CPzqKqqsHI"
    "&token=YJVj-JROo0NRKnHP0QvIbve3aTVxE700D4wICDzAZb0"
)

# URL de la Web App de Google Apps Script (ya no se usa en modo PULL)
SHEETS_WEBHOOK_URL = "https://script.google.com/a/macros/natura.net/s/AKfycbwj0UEFbvAK4Zy-6Xm_guaa_ctMnS4pDB6Dx0ydfe9ylq7ozgDg5Q-33sHp-rtMxU6NYQ/exec"

# Archivo JSON que Apps Script va a leer desde GitHub (modo PULL)
SHEETS_JSON_FILE = "sin_stock_sheets.json"

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

# ─── Google Sheets (via Apps Script) ────────────────────────────────────────

def guardar_json_para_sheets(productos_con_estado: list):
    """
    Guarda un JSON en el repositorio con los productos sin stock.
    Apps Script lo va a leer cada 3 horas desde GitHub (modo PULL),
    así no hace falta exponer la Web App públicamente.
    """
    if not productos_con_estado:
        # Igual escribimos un JSON vacío para que Apps Script no falle
        datos = {"productos": [], "fecha_generado": datetime.now().isoformat()}
    else:
        fecha_hora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        datos = {
            "productos": [
                {
                    "codigo": p["codigo"],
                    "nombre": p["nombre"],
                    "fecha": fecha_hora,
                    "es_nuevo": p["es_nuevo"],
                    "url": p["url"],
                }
                for p in productos_con_estado
            ],
            "fecha_generado": datetime.now().isoformat(),
        }

    with open(SHEETS_JSON_FILE, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)

    print(f"📄 JSON para Sheets guardado: {SHEETS_JSON_FILE} ({len(datos['productos'])} producto(s))")

# ─── Webhook Google Chat (resumen corto) ────────────────────────────────────

def enviar_resumen_chat(total: int, nuevos: int):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    link_sheet = "👉 Revisá el detalle completo en la Google Sheet."
    payload = {"text": (
        f"✅ Chequeo Natura Argentina completado [{now}]\n"
        f"Productos sin stock: {total}\n"
        f"Nuevos detectados: {nuevos}\n"
        f"{link_sheet}"
    )}
    try:
        r = requests.post(WEBHOOK_CHAT_URL, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"❌ Error resumen chat: {e}")

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
        print(f"    ⚠️  Error obteniendo SKU de {url}: {e}")
    return "cod. No detectado"

def escanear_argentina(driver) -> list:
    print(f"🌐 Cargando {URL_ARGENTINA} ...")
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

    print(f"✅ Todos los productos cargados ({clics} clics).")
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
            print(f"  ⚠️  Error procesando producto: {e}")

    print(f"📦 Productos sin stock encontrados: {len(sin_stock)}")
    return sin_stock

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(f"🤖 Natura Stock Bot Argentina - {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print("=" * 60)

    memoria = cargar_memoria()
    memoria = limpiar_viejos(memoria)

    driver = crear_driver()

    try:
        sin_stock = escanear_argentina(driver)
        productos_para_sheet = []

        print(f"\n🔍 Verificando memoria para {len(sin_stock)} productos sin stock...")
        for producto in sin_stock:
            if producto["necesita_visita"]:
                print(f"  → Visitando ficha: {producto['nombre']}")
                producto["codigo"] = obtener_sku_desde_pagina(driver, producto["url"])

            codigo = producto["codigo"]
            nombre = producto["nombre"]

            es_nuevo = not ya_notificado(memoria, codigo)
            print(f"  • {nombre} | {codigo} | {'NUEVO' if es_nuevo else 'ya visto'}")

            if es_nuevo:
                marcar_notificado(memoria, codigo, nombre)

            # Mandamos TODOS los productos sin stock a la Sheet,
            # marcando si son nuevos en esta ejecución o no.
            productos_para_sheet.append({
                "codigo": codigo,
                "nombre": nombre,
                "url": producto["url"],
                "es_nuevo": es_nuevo,
            })

    finally:
        driver.quit()

    guardar_memoria(memoria)

    nuevos_count = sum(1 for p in productos_para_sheet if p["es_nuevo"])

    print(f"\n📣 Total sin stock: {len(productos_para_sheet)} | Nuevos: {nuevos_count}")

    if productos_para_sheet:
        guardar_json_para_sheets(productos_para_sheet)
    else:
        guardar_json_para_sheets([])

    enviar_resumen_chat(total=len(productos_para_sheet), nuevos=nuevos_count)
    print("✅ Bot finalizado.")


if __name__ == "__main__":
    main()
