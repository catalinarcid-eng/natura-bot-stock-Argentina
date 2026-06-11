
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
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
 
# ─── Memoria (JSON con TTL de 7 días) ────────────────────────────────────────
 
def cargar_memoria() -> dict:
    if not os.path.exists(MEMORIA_FILE):
        return {}
    with open(MEMORIA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
 
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
    lineas = ["🚨 *Productos SIN STOCK en Natura Argentina:*\n"]
    for p in productos:
        lineas.append(f"• *{p['nombre']}*\n  Código: {p['codigo']}\n  🔗 {p['url']}")
    payload = {"text": "\n".join(lineas)}
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
        print(f"✅ Webhook enviado: {len(productos)} producto(s).")
    except Exception as e:
        print(f"❌ Error webhook: {e}")
 
def enviar_resumen(total: int, nuevos: int):
    now = datetime.now().strftime("%d/%m/%Y %H:%M")
    payload = {"text": (
        f"✅ *Chequeo Natura Argentina completado* [{now}]\n"
        f"Productos revisados: {total}\n"
        f"Nuevos sin stock notificados: {nuevos}"
    )}
    try:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
    except Exception as e:
        print(f"❌ Error resumen: {e}")
 
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
    return webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=opts
    )
 
def obtener_sku_desde_url(url: str) -> str:
    """Extrae el código NATARG-XXX directo de la URL del producto."""
    match = re.search(r'(NAT[A-Z]+-\d+)', url, re.IGNORECASE)
    if match:
        return f"cod. {match.group(1).upper()}"
    return "cod. No detectado"
 
def obtener_sku_desde_pagina(driver, url: str) -> str:
    """Si no está en la URL, visita la ficha y extrae el código."""
    try:
        driver.get(url)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        # Buscar <p class="text-xs text-low-emphasis">cod. NATARG-XXX</p>
        for p in soup.find_all("p"):
            texto = p.get_text(strip=True)
            if texto.lower().startswith("cod."):
                return texto
        # Fallback: buscar en todo el texto
        texto_pagina = soup.get_text(separator=" ")
        match = re.search(r'(cod\.\s*NAT[A-Z]+-\d+)', texto_pagina, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    except Exception as e:
        print(f"    ⚠️  Error obteniendo SKU de {url}: {e}")
    return "cod. No detectado"
 
def escanear_argentina(driver) -> list:
    """
    Carga todos los productos de Natura Argentina y retorna
    los que están sin stock con nombre, código y URL.
    """
    print(f"🌐 Cargando {URL_ARGENTINA} ...")
    driver.get(URL_ARGENTINA)
    time.sleep(8)
 
    # Presionar "explorar más resultados" hasta el final
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
 
    # Buscar h4 que contengan "producto agotado"
    for h4 in soup.find_all("h4"):
        texto_h4 = h4.get_text(strip=True).lower()
        if "producto agotado" not in texto_h4:
            continue
 
        try:
            # Subir en el DOM para encontrar el enlace del producto
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
 
            # Nombre del producto
            nombre = ""
            # Primero desde aria-label del enlace
            if a_tag.get("aria-label"):
                nombre = a_tag["aria-label"].strip()
            # Luego desde h4 dentro del enlace que NO sea "producto agotado"
            if not nombre:
                for h4_inner in a_tag.find_all("h4"):
                    t = h4_inner.get_text(strip=True)
                    if "producto agotado" not in t.lower() and t:
                        nombre = t
                        break
            # Fallback: texto del enlace limpio
            if not nombre:
                nombre = a_tag.get_text(separator=" ", strip=True)
                nombre = re.sub(r'producto agotado', '', nombre, flags=re.IGNORECASE).strip()
            if not nombre:
                nombre = "Producto sin nombre"
 
            # Código SKU
            codigo = obtener_sku_desde_url(href)
 
            sin_stock.append({
                "nombre": nombre,
                "codigo": codigo,
                "url": href,
                "necesita_visita": codigo == "cod. No detectado",
            })
 
        except Exception as e:
            print(f"  ⚠️  Error procesando producto agotado: {e}")
 
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
 
        nuevos_sin_stock = []
        print(f"\n🔍 Verificando memoria para {len(sin_stock)} productos sin stock...")
 
        for producto in sin_stock:
            # Si el código no estaba en la URL, visitar la ficha
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
 
    print(f"\n📣 Nuevos sin stock para notificar: {len(nuevos_sin_stock)}")
    if nuevos_sin_stock:
        enviar_webhook(nuevos_sin_stock)
 
    enviar_resumen(total=len(sin_stock), nuevos=len(nuevos_sin_stock))
    print("✅ Bot finalizado.")
 
 
if __name__ == "__main__":
    main()
