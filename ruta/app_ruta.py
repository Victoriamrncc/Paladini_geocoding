#!/usr/bin/env python3
"""
app_ruta.py — Servidor local de la app "Hoja de ruta" (Paladini).

App INDEPENDIENTE del visor: no importa ni modifica app_visor.py / visor.html,
usa su propio puerto y solo LEE los archivos de datos.

QUÉ HACE
  - Sirve ruta.html por http://localhost:8001/
  - Expone /api/sucursales, /api/clientes, /api/seguimiento, /api/flota,
    /api/tipos y /api/horarios (CSV leídos del disco en cada pedido) y
    /api/osrm (proxy de ruteo por calles).
  - Abre el navegador al arrancar.

CÓMO EJECUTARLO
    python app_ruta.py

Dejá la terminal abierta mientras uses la app. Para cortar: Ctrl+C.
Si algún archivo tiene otro nombre o está en otra carpeta, ajustá la
CONFIGURACIÓN de abajo.
"""

import json
import threading
import time
import unicodedata
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN — mismos valores por defecto que app_visor.py
# ─────────────────────────────────────────────────────────────────────────

PUERTO = 8001  # el visor usa 8000: así pueden convivir

CARPETA_DATOS = Path(r"C:\Proyectos\Paladini_geocoding\data")

NOMBRE_POR_DEFECTO = {
    "sucursales": "sucursales.csv",
    "clientes": "Geolocalizacion_RioNegro_Neuquen_anonimizado.csv",
    "seguimiento": "Detalle_entregas_anonimizado.csv",
    "flota": "flota.csv",
    "tipos": "tipos_camion.csv",
    "horarios": "ventanas_horarias.csv",
}

# Si no está el nombre exacto, se busca UN único archivo cuyo nombre contenga
# alguno de estos textos (sin importar mayúsculas ni acentos).
PATRONES_BUSQUEDA = {
    "sucursales": ["sucursal"],
    "clientes": ["cliente", "geolocaliz"],
    "seguimiento": ["seguimiento", "entrega", "logistic"],
    "flota": ["flota"],
    "tipos": ["tipos_camion"],
    "horarios": ["ventanas_horarias"],
}

EXTENSIONES_VALIDAS = (".csv", ".xlsx", ".xls")

CARPETA_APP = Path(__file__).resolve().parent

# Archivos de la interfaz que se sirven (ruta URL -> archivo, tipo de contenido)
ESTATICOS = {
    "/": ("ruta.html", "text/html; charset=utf-8"),
    "/ruta.html": ("ruta.html", "text/html; charset=utf-8"),
    "/ruta.css": ("ruta.css", "text/css; charset=utf-8"),
    "/ruta_logica.js": ("ruta_logica.js", "application/javascript; charset=utf-8"),
    "/ruta_algoritmo.js": ("ruta_algoritmo.js", "application/javascript; charset=utf-8"),
    "/ruta_app.js": ("ruta_app.js", "application/javascript; charset=utf-8"),
}

# ─────────────────────────────────────────────────────────────────────────
# Resolución y lectura de archivos (copiado de app_visor.py)
# ─────────────────────────────────────────────────────────────────────────


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar nombres de archivo."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower()


def buscar_por_patron(clave: str, carpeta: Path):
    """Un único archivo cuyo nombre contenga algún patrón de la clave; si no
    hay ninguno o hay varios, None (no se adivina)."""
    if clave not in PATRONES_BUSQUEDA or not carpeta.is_dir():
        return None
    patrones = [normalizar(p) for p in PATRONES_BUSQUEDA[clave]]
    candidatos = [
        a for a in carpeta.iterdir()
        if a.is_file()
        and a.suffix.lower() in EXTENSIONES_VALIDAS
        and any(p in normalizar(a.stem) for p in patrones)
    ]
    return candidatos[0] if len(candidatos) == 1 else None


def resolver_archivo(clave: str) -> Path:
    exacta = CARPETA_DATOS / NOMBRE_POR_DEFECTO[clave]
    if exacta.is_file():
        return exacta
    return buscar_por_patron(clave, CARPETA_DATOS) or exacta


def leer_como_csv_texto(ruta: Path) -> str:
    """Lee un CSV o Excel local y lo devuelve como texto CSV."""
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró el archivo: {ruta}")

    if ruta.suffix.lower() in (".xlsx", ".xls"):
        import pandas as pd  # solo hace falta si hay Excel

        return pd.read_excel(ruta).to_csv(index=False)

    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return ruta.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return ruta.read_text(encoding="utf-8", errors="replace")


# ─────────────────────────────────────────────────────────────────────────
# Servidor
# ─────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────
# OSRM (ruteo por calles) — copiado de app_visor.py
# ─────────────────────────────────────────────────────────────────────────
# Servidor público de PRUEBA: no es para producción, admite ~1 pedido por
# segundo y solo tiene perfil de auto. Cuando exista un OSRM propio (Docker),
# basta con cambiar esta URL, por ejemplo "http://localhost:5000".
OSRM_URL = "https://router.project-osrm.org"
OSRM_PAUSA_S = 1.1          # pausa mínima entre pedidos al servidor público
_osrm_lock = threading.Lock()
_osrm_cache: dict = {}
_osrm_ultimo = [0.0]


def consultar_osrm(payload: dict) -> dict:
    """payload: {servicio: 'table'|'route', coords: [[lat, lon], ...],
    sources?: [i], destinations?: [j]}. Devuelve el JSON de OSRM."""
    servicio = payload.get("servicio")
    coords = payload.get("coords") or []
    if servicio not in ("table", "route") or not (2 <= len(coords) <= 100):
        raise ValueError("pedido OSRM inválido (servicio o cantidad de puntos)")
    cadena = ";".join(f"{float(lon):.6f},{float(lat):.6f}" for lat, lon in coords)
    if servicio == "table":
        url = f"{OSRM_URL}/table/v1/driving/{cadena}?annotations=distance,duration"
        for clave in ("sources", "destinations"):
            if payload.get(clave) is not None:
                url += f"&{clave}=" + ";".join(str(int(i)) for i in payload[clave])
    else:
        url = f"{OSRM_URL}/route/v1/driving/{cadena}?overview=full&geometries=geojson"
    with _osrm_lock:
        if url in _osrm_cache:
            return _osrm_cache[url]
        espera = OSRM_PAUSA_S - (time.time() - _osrm_ultimo[0])
        if espera > 0:
            time.sleep(espera)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PaladiniVisor/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                datos = json.loads(r.read().decode("utf-8"))
        finally:
            _osrm_ultimo[0] = time.time()
    if datos.get("code") != "Ok":
        raise RuntimeError(datos.get("message") or datos.get("code") or "respuesta inválida")
    _osrm_cache[url] = datos
    return datos


class RutaHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # sin log por pedido

    def _responder(self, codigo: int, cuerpo: bytes, content_type: str):
        self.send_response(codigo)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _error(self, codigo: int, mensaje: str):
        self._responder(codigo, mensaje.encode("utf-8"), "text/plain; charset=utf-8")

    def do_GET(self):
        ruta = self.path.split("?", 1)[0]

        if ruta.startswith("/api/") and ruta[5:] in NOMBRE_POR_DEFECTO:
            archivo = resolver_archivo(ruta[5:])
            try:
                texto = leer_como_csv_texto(archivo)
            except FileNotFoundError as e:
                self._error(404, str(e))
                return
            except Exception as e:  # noqa: BLE001
                self._error(500, f"Error leyendo {archivo.name}: {e}")
                return
            self._responder(200, texto.encode("utf-8"), "text/csv; charset=utf-8")
            return

        if ruta in ESTATICOS:
            nombre, tipo = ESTATICOS[ruta]
            archivo = CARPETA_APP / nombre
            if not archivo.exists():
                self._error(404, f"Falta {nombre} junto a app_ruta.py")
                return
            self._responder(200, archivo.read_bytes(), tipo)
            return

        self._error(404, "No encontrado")

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/osrm":
            self._error(404, "No encontrado")
            return
        try:
            largo = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(largo).decode("utf-8") or "{}")
            codigo, datos = 200, consultar_osrm(payload)
        except Exception as e:  # sin red, límite del servidor, pedido inválido
            codigo, datos = 502, {"error": f"{type(e).__name__}: {e}"}
        self._responder(codigo, json.dumps(datos).encode("utf-8"), "application/json; charset=utf-8")


def main():
    url = f"http://localhost:{PUERTO}/"
    try:
        servidor = ThreadingHTTPServer(("localhost", PUERTO), RutaHandler)
    except OSError as e:
        print(f"No se pudo abrir el puerto {PUERTO}: {e}")
        print("Puede que ya haya otra copia de app_ruta.py corriendo. "
              "Cerrala, o cambiá PUERTO arriba en este archivo.")
        return

    print(f"Hoja de ruta corriendo en {url}")
    print(f"Carpeta de datos: {CARPETA_DATOS}")
    for clave in NOMBRE_POR_DEFECTO:
        ruta = resolver_archivo(clave)
        marca = "" if ruta.is_file() else "  [NO SE ENCONTRÓ]"
        print(f"  - {clave}: {ruta}{marca}")
    print("Presioná Ctrl+C para detener el servidor.")

    threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        servidor.shutdown()


if __name__ == "__main__":
    main()