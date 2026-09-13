#!/usr/bin/env python3
"""
app_visor.py — Servidor local para el Visor de Direcciones (Paladini).

QUÉ HACE
  - Sirve visor.html por http://localhost:PUERTO/
  - Expone 3 endpoints que leen tus archivos locales y los devuelven como CSV:
      /api/sucursales   -> archivo de sucursales
      /api/clientes     -> CSV de geolocalización de clientes
      /api/seguimiento  -> archivo de seguimiento/logística (se relee del
                            disco en CADA pedido, así siempre devuelve lo
                            último que se haya guardado ahí)
  - Abre el navegador automáticamente al arrancar.

No agrega dependencias nuevas: usa solo la librería estándar de Python, más
pandas/openpyxl (que ya tenías) únicamente si el archivo de seguimiento es
un .xlsx.

CÓMO EJECUTARLO
    python app_visor.py

Dejá esta terminal abierta mientras uses el visor. Para cortar el servidor,
Ctrl+C.

CONFIGURACIÓN — revisá y ajustá esta sección a tu entorno.
"""

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN — ajustá estas rutas y nombres a tu entorno real
# ─────────────────────────────────────────────────────────────────────────

PUERTO = 8000

# Carpeta donde están tus archivos de datos.
CARPETA_DATOS = Path(r"C:\Proyectos\Paladini_geocoding\data")

# Nombres de archivo dentro de CARPETA_DATOS.
# Cambiá estos nombres si en tu carpeta se llaman distinto.
# ARCHIVO_SEGUIMIENTO puede ser .csv o .xlsx: se detecta por la extensión.
ARCHIVO_SUCURSALES = "sucursales.csv"
ARCHIVO_CLIENTES = "Geolocalizacion_RioNegro_Neuquen_anonimizado.csv"
ARCHIVO_SEGUIMIENTO = "Detalle_entregas_anonimizado.csv"

# Carpeta donde vive este script. visor.html debe estar en la misma carpeta.
CARPETA_APP = Path(__file__).resolve().parent
ARCHIVO_HTML = "visor.html"

# ─────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN EDITABLE DESDE EL VISOR (clientes / seguimiento)
# ─────────────────────────────────────────────────────────────────────────
# Además de las rutas fijas de arriba, el usuario puede indicar desde el
# propio visor (botón "Configuración" o cuando falla la carga automática)
# una ruta distinta para el archivo de clientes y/o el de seguimiento. Esa
# elección se guarda acá, en un JSON al lado de este script, y tiene
# prioridad sobre ARCHIVO_CLIENTES / ARCHIVO_SEGUIMIENTO de arriba.
#
# Las rutas hardcodeadas de CONFIGURACIÓN nunca se pisan: siguen siendo el
# valor por defecto que se intenta primero (o que vuelve a usarse si el
# usuario "restablece" desde el visor). Sucursales NO tiene override: usa
# siempre CARPETA_DATOS / ARCHIVO_SUCURSALES.

ARCHIVO_CONFIG_OVERRIDES = CARPETA_APP / "config_visor.json"

# Claves permitidas en el archivo de overrides.
CLAVES_OVERRIDE = ("clientes", "seguimiento")


def cargar_overrides() -> dict:
    """Lee config_visor.json si existe. Si no existe o está corrupto, {}."""
    if not ARCHIVO_CONFIG_OVERRIDES.exists():
        return {}
    try:
        with ARCHIVO_CONFIG_OVERRIDES.open("r", encoding="utf-8") as f:
            datos = json.load(f)
        if not isinstance(datos, dict):
            return {}
        return {k: v for k, v in datos.items() if k in CLAVES_OVERRIDE and v}
    except (json.JSONDecodeError, OSError):
        return {}


def guardar_overrides(overrides: dict) -> None:
    with ARCHIVO_CONFIG_OVERRIDES.open("w", encoding="utf-8") as f:
        json.dump(overrides, f, ensure_ascii=False, indent=2)


def ruta_default(clave: str) -> Path:
    return {
        "clientes": CARPETA_DATOS / ARCHIVO_CLIENTES,
        "seguimiento": CARPETA_DATOS / ARCHIVO_SEGUIMIENTO,
    }[clave]


def ruta_efectiva(clave: str) -> Path:
    """Ruta que se usa realmente: override guardado por el usuario, o la
    ruta hardcodeada en CONFIGURACIÓN si no hay override."""
    overrides = cargar_overrides()
    if clave in overrides:
        return Path(overrides[clave])
    return ruta_default(clave)


def estado_config() -> dict:
    """Info para mostrar en el visor: ruta efectiva, origen y si existe."""
    overrides = cargar_overrides()
    salida = {}
    for clave in CLAVES_OVERRIDE:
        ruta = ruta_efectiva(clave)
        salida[clave] = {
            "ruta": str(ruta),
            "origen": "personalizada" if clave in overrides else "por_defecto",
            "ruta_por_defecto": str(ruta_default(clave)),
            "existe": ruta.exists(),
        }
    return salida


# ─────────────────────────────────────────────────────────────────────────


def leer_como_csv_texto(ruta: Path) -> str:
    """
    Lee un archivo de datos local (CSV o Excel) y lo devuelve como texto CSV.

    - Si la extensión es .xlsx/.xls, usa pandas/openpyxl para convertirlo.
    - Si es .csv, lo devuelve tal cual está en disco (probando utf-8 y,
      si falla, latin-1, que es común en CSV exportados desde Excel/Windows).
    """
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {ruta}")

    if ruta.suffix.lower() in (".xlsx", ".xls"):
        import pandas as pd  # import diferido: solo hace falta si hay Excel

        df = pd.read_excel(ruta)
        return df.to_csv(index=False)

    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return ruta.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    # último recurso: no debería llegar acá, pero evita romper el servidor
    return ruta.read_text(encoding="utf-8", errors="replace")


class VisorHandler(BaseHTTPRequestHandler):

    # Silencia el log verboso por defecto de http.server.
    # Comentá este método (o hacé que llame a super().log_message(...))
    # si en algún momento querés ver cada pedido en la terminal.
    def log_message(self, format, *args):
        pass

    def _responder_csv(self, ruta_archivo: Path):
        try:
            texto = leer_como_csv_texto(ruta_archivo)
        except FileNotFoundError as e:
            self._responder_error(404, str(e))
            return
        except Exception as e:  # noqa: BLE001 - queremos capturar cualquier error de lectura
            self._responder_error(500, f"Error leyendo {ruta_archivo.name}: {e}")
            return

        cuerpo = texto.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _responder_error(self, codigo: int, mensaje: str):
        cuerpo = mensaje.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def _responder_json(self, codigo: int, datos: dict):
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _responder_archivo_estatico(self, nombre_archivo: str, content_type: str):
        ruta = CARPETA_APP / nombre_archivo
        if not ruta.exists():
            self._responder_error(404, f"No se encontró {nombre_archivo} junto a app_visor.py")
            return
        cuerpo = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        ruta = self.path.split("?", 1)[0]

        rutas_api = {
            "/api/sucursales": CARPETA_DATOS / ARCHIVO_SUCURSALES,
            "/api/clientes": ruta_efectiva("clientes"),
            "/api/seguimiento": ruta_efectiva("seguimiento"),
        }

        if ruta in rutas_api:
            self._responder_csv(rutas_api[ruta])
            return

        if ruta == "/api/config":
            self._responder_json(200, estado_config())
            return

        if ruta in ("/", "/index.html", f"/{ARCHIVO_HTML}"):
            self._responder_archivo_estatico(ARCHIVO_HTML, "text/html; charset=utf-8")
            return

        self._responder_error(404, "No encontrado")

    def do_POST(self):
        ruta = self.path.split("?", 1)[0]

        if ruta != "/api/config":
            self._responder_error(404, "No encontrado")
            return

        try:
            largo = int(self.headers.get("Content-Length", 0))
            cuerpo = self.rfile.read(largo) if largo else b""
            payload = json.loads(cuerpo.decode("utf-8")) if cuerpo else {}
            if not isinstance(payload, dict):
                raise ValueError("El cuerpo debe ser un objeto JSON")
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
            self._responder_json(400, {"ok": False, "error": f"JSON inválido: {e}"})
            return

        overrides = cargar_overrides()
        errores = {}

        for clave in CLAVES_OVERRIDE:
            if clave not in payload:
                continue
            valor = payload[clave]

            # Valor vacío / null -> restablecer al valor por defecto (sacar el override)
            if valor is None or (isinstance(valor, str) and valor.strip() == ""):
                overrides.pop(clave, None)
                continue

            if not isinstance(valor, str):
                errores[clave] = "La ruta debe ser texto"
                continue

            candidata = Path(valor)
            if not candidata.exists() or not candidata.is_file():
                errores[clave] = f"No se encontró el archivo en esa ruta: {valor}"
                continue

            overrides[clave] = str(candidata)

        if errores:
            # No guardamos nada de la tanda si alguna ruta pedida es inválida,
            # para no dejar el config a medio actualizar de forma confusa.
            self._responder_json(400, {"ok": False, "errores": errores, **estado_config()})
            return

        guardar_overrides(overrides)
        self._responder_json(200, {"ok": True, **estado_config()})


def abrir_navegador(url: str):
    # Pequeña espera para asegurarnos de que el servidor ya esté escuchando
    # antes de intentar abrir la pestaña.
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()


def main():
    url = f"http://localhost:{PUERTO}/"
    servidor = ThreadingHTTPServer(("localhost", PUERTO), VisorHandler)

    estado = estado_config()
    print(f"Visor Paladini corriendo en {url}")
    print(f"Carpeta de datos configurada: {CARPETA_DATOS}")
    print(f"  - Sucursales:  {CARPETA_DATOS / ARCHIVO_SUCURSALES}  (fija, sin override)")
    for clave, etiqueta in (("clientes", "Clientes"), ("seguimiento", "Seguimiento")):
        info = estado[clave]
        origen = "ruta personalizada" if info["origen"] == "personalizada" else "valor por defecto"
        print(f"  - {etiqueta}: {info['ruta']}  ({origen})")
    print("Podés cambiar las rutas de Clientes/Seguimiento desde el botón "
          "\"Configuración\" del visor sin editar este archivo.")
    print("Presioná Ctrl+C para detener el servidor.")

    abrir_navegador(url)

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        servidor.shutdown()


if __name__ == "__main__":
    main()