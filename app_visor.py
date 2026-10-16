#!/usr/bin/env python3
"""
app_visor.py — Servidor local para el Visor de Direcciones (Paladini).

QUÉ HACE
  - Sirve visor.html por http://localhost:PUERTO/
  - Expone endpoints que leen tus archivos locales y los devuelven como CSV:
      /api/sucursales   -> archivo de sucursales
      /api/clientes     -> CSV de geolocalización de clientes
      /api/seguimiento  -> archivo de seguimiento/logística (se relee del
                            disco en CADA pedido, así siempre devuelve lo
                            último que se haya guardado ahí)
  - Permite elegir esos archivos con el explorador de Windows, desde el
    propio visor (botón "Elegir archivo…"), sin copiar rutas a mano.
  - Abre el navegador automáticamente al arrancar.

No agrega dependencias nuevas: usa solo la librería estándar de Python
(incluido tkinter, que viene con Python en Windows y macOS), más
pandas/openpyxl (que ya tenías) únicamente si algún archivo es .xlsx.

CÓMO EJECUTARLO
    python app_visor.py

Dejá esta terminal abierta mientras uses el visor. Para cortar el servidor,
Ctrl+C.

CONFIGURACIÓN — revisá y ajustá esta sección a tu entorno.
"""

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN — ajustá estas rutas y nombres a tu entorno real
# ─────────────────────────────────────────────────────────────────────────

PUERTO = 8000

# Carpeta donde están tus archivos de datos.
# Es solo el valor POR DEFECTO: desde el visor se puede elegir otra carpeta
# con el explorador, y esa elección tiene prioridad (ver más abajo).
CARPETA_DATOS = Path(r"C:\Proyectos\Paladini_geocoding\data")

# Nombres de archivo dentro de CARPETA_DATOS.
# Cambiá estos nombres si en tu carpeta se llaman distinto.
# Cualquiera de los tres puede ser .csv o .xlsx: se detecta por la extensión.
ARCHIVO_SUCURSALES = "sucursales.csv"
ARCHIVO_CLIENTES = "Geolocalizacion_RioNegro_Neuquen_anonimizado.csv"
ARCHIVO_SEGUIMIENTO = "Detalle_entregas_anonimizado.csv"

# Carpeta donde vive este script. visor.html debe estar en la misma carpeta.
CARPETA_APP = Path(__file__).resolve().parent
ARCHIVO_HTML = "visor.html"

# ─────────────────────────────────────────────────────────────────────────
# CONFIGURACIÓN EDITABLE DESDE EL VISOR
# ─────────────────────────────────────────────────────────────────────────
# Además de las rutas fijas de arriba, el usuario puede indicar desde el
# propio visor (botón "Configuración" o cuando falla la carga automática):
#
#   - una carpeta de datos distinta (reemplaza a CARPETA_DATOS), y/o
#   - un archivo puntual distinto para clientes, seguimiento y sucursales.
#
# Esa elección se guarda acá, en un JSON al lado de este script, y tiene
# prioridad sobre lo hardcodeado arriba. Las rutas de CONFIGURACIÓN nunca
# se pisan: siguen siendo el valor por defecto que se intenta primero (o al
# que se vuelve si el usuario "restablece" desde el visor).
#
# Orden de resolución de cada archivo:
#   1. override propio del archivo, si existe;
#   2. si no, <carpeta efectiva> / <nombre por defecto del archivo>;
#      donde <carpeta efectiva> es el override de carpeta o CARPETA_DATOS.

ARCHIVO_CONFIG_OVERRIDES = CARPETA_APP / "config_visor.json"

# Claves de archivo individual.
CLAVES_ARCHIVO = ("clientes", "seguimiento", "sucursales")

# Todas las claves válidas dentro del JSON de overrides.
CLAVES_OVERRIDE = ("carpeta_datos",) + CLAVES_ARCHIVO

# Nombre por defecto de cada archivo dentro de la carpeta de datos.
NOMBRE_POR_DEFECTO = {
    "clientes": ARCHIVO_CLIENTES,
    "seguimiento": ARCHIVO_SEGUIMIENTO,
    "sucursales": ARCHIVO_SUCURSALES,
}

ETIQUETA = {
    "carpeta_datos": "Carpeta de datos",
    "clientes": "Clientes",
    "seguimiento": "Seguimiento",
    "sucursales": "Sucursales",
}

# Si dentro de la carpeta de datos no hay un archivo con el nombre EXACTO de
# arriba (NOMBRE_POR_DEFECTO), el visor igual intenta reconocerlo buscando
# estos textos en el nombre del archivo (sin importar mayúsculas/minúsculas
# ni acentos). Alcanza con que el nombre CONTENGA alguno de estos textos.
#
# Sólo lo toma en forma automática si encuentra UN ÚNICO archivo que
# matchee: si no encuentra ninguno, o encuentra varios (ambiguo), no
# adivina — se sigue pidiendo elegir ese archivo a mano, igual que ahora.
#
# Agregá o cambiá estos textos si tus archivos reales se llaman distinto.
PATRONES_BUSQUEDA = {
    "sucursales": ["sucursal"],
    "clientes": ["cliente", "geolocaliz"],
    "seguimiento": ["seguimiento", "entrega", "logistic"],
    # Hoja de ruta (archivos extra, no pasan por la configuración del visor)
    "flota": ["flota"],
    "tipos": ["tipos_camion"],
}

# Extensiones que se consideran candidatas al buscar por patrón.
EXTENSIONES_VALIDAS = (".csv", ".xlsx", ".xls")


def normalizar(texto: str) -> str:
    """minúsculas y sin acentos, para comparar nombres de archivo sin
    depender de tildes/mayúsculas."""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return texto.lower()


def buscar_por_patron(clave: str, carpeta: Path) -> Path | None:
    """Si no hay un archivo con el nombre exacto por defecto, busca dentro
    de la carpeta un único archivo cuyo nombre contenga alguno de los
    PATRONES_BUSQUEDA de esta clave. Devuelve ese archivo, o None si no
    encontró ninguno o encontró varios (en ese caso hay que elegirlo a
    mano, para no arriesgar a cargar el archivo equivocado)."""
    if clave not in PATRONES_BUSQUEDA or not carpeta.is_dir():
        return None

    patrones = [normalizar(p) for p in PATRONES_BUSQUEDA[clave]]
    candidatos = []
    for archivo in carpeta.iterdir():
        if not archivo.is_file() or archivo.suffix.lower() not in EXTENSIONES_VALIDAS:
            continue
        nombre_norm = normalizar(archivo.stem)
        if any(p in nombre_norm for p in patrones):
            candidatos.append(archivo)

    if len(candidatos) == 1:
        return candidatos[0]
    return None


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


def carpeta_efectiva(overrides: dict = None) -> Path:
    """Carpeta de datos que se usa realmente: la elegida por el usuario, o
    CARPETA_DATOS si nunca eligió ninguna."""
    if overrides is None:
        overrides = cargar_overrides()
    if "carpeta_datos" in overrides:
        return Path(overrides["carpeta_datos"])
    return CARPETA_DATOS


def ruta_default(clave: str, overrides: dict = None) -> Path:
    """Ruta que tendría el archivo si no hubiera override propio.

    Primero prueba el nombre exacto por defecto dentro de la carpeta
    efectiva. Si ese archivo no está, busca por PATRONES_BUSQUEDA (ver
    buscar_por_patron): si hay un único archivo cuyo nombre coincide, se usa
    ese. Si no hay ninguno o hay varios posibles, se devuelve igual la ruta
    del nombre exacto (que va a dar "no existe"), para que quede claro que
    hay que elegir el archivo a mano."""
    carpeta = carpeta_efectiva(overrides)
    ruta_exacta = carpeta / NOMBRE_POR_DEFECTO[clave]
    if ruta_exacta.is_file():
        return ruta_exacta

    encontrado = buscar_por_patron(clave, carpeta)
    if encontrado is not None:
        return encontrado

    return ruta_exacta


def ruta_efectiva(clave: str) -> Path:
    """Ruta que se usa realmente para leer este archivo."""
    overrides = cargar_overrides()
    if clave in overrides:
        return Path(overrides[clave])
    return ruta_default(clave, overrides)


# ─────────────────────────────────────────────────────────────────────────
# OSRM (ruteo por calles) — usado por la pestaña "Hoja de ruta"
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
        url = f"{OSRM_URL}/table/v1/driving/{cadena}?annotations=distance"
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


def ruta_extra(clave: str) -> Path:
    """Archivos de la pestaña Hoja de ruta: flota.csv y tipos_camion.csv, en la
    carpeta de datos efectiva (o encontrados por patrón en el nombre)."""
    carpeta = carpeta_efectiva(cargar_overrides())
    exacta = carpeta / {"flota": "flota.csv", "tipos": "tipos_camion.csv"}[clave]
    if exacta.is_file():
        return exacta
    return buscar_por_patron(clave, carpeta) or exacta


def estado_config() -> dict:
    """Info para mostrar en el visor: ruta efectiva, origen y si existe."""
    overrides = cargar_overrides()
    carpeta = carpeta_efectiva(overrides)

    salida = {
        "carpeta_datos": {
            "ruta": str(carpeta),
            "origen": "personalizada" if "carpeta_datos" in overrides else "por_defecto",
            "ruta_por_defecto": str(CARPETA_DATOS),
            "existe": carpeta.is_dir(),
        },
        # El visor usa esto para mostrar u ocultar los botones
        # "Elegir archivo…" / "Elegir carpeta…".
        "dialogo_disponible": dialogo_disponible(),
    }

    for clave in CLAVES_ARCHIVO:
        ruta = ruta_efectiva(clave)
        salida[clave] = {
            "ruta": str(ruta),
            "origen": "personalizada" if clave in overrides else "por_defecto",
            "ruta_por_defecto": str(ruta_default(clave, overrides)),
            "existe": ruta.is_file(),
            "nombre_por_defecto": NOMBRE_POR_DEFECTO[clave],
        }
    return salida


def limpiar_valor_ruta(valor: str) -> str:
    """Windows: si la ruta se pegó con "Copiar como ruta de acceso" desde el
    Explorador, viene envuelta en comillas. Sin sacarlas, ninguna ruta
    "existe" nunca y el guardado falla siempre."""
    valor = valor.strip()
    if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in ('"', "'"):
        valor = valor[1:-1].strip()
    return valor


def validar_ruta(clave: str, valor: str):
    """Devuelve (Path validada, None) o (None, mensaje de error)."""
    candidata = Path(limpiar_valor_ruta(valor))

    if clave == "carpeta_datos":
        if candidata.is_file():
            return None, f"Esa ruta es un archivo, no una carpeta: {candidata}"
        if not candidata.is_dir():
            return None, f"No se encontró la carpeta: {candidata}"
        return candidata, None

    if candidata.is_dir():
        return None, f"Esa ruta es una carpeta, no el archivo en sí: {candidata}"
    if not candidata.is_file():
        return None, f"No se encontró el archivo en esa ruta: {candidata}"
    return candidata, None


# ─────────────────────────────────────────────────────────────────────────
# SELECTOR NATIVO DE ARCHIVOS / CARPETAS
# ─────────────────────────────────────────────────────────────────────────
# El navegador, por seguridad, nunca le da a una página la ruta real de un
# archivo (devuelve "C:\fakepath\..."). Pero este servidor corre en la misma
# máquina que el usuario, así que puede abrir el explorador nativo de Windows
# y quedarse con la ruta completa.
#
# El diálogo se abre en un PROCESO APARTE (mismo intérprete de Python, con
# -c) por dos motivos:
#   - tkinter tiene que correr en el hilo principal, y acá cada pedido HTTP
#     se atiende en un hilo distinto;
#   - si el usuario cierra mal la ventana, se lleva puesto solo ese proceso
#     y no el servidor.

_LOCK_DIALOGO = threading.Lock()

# Timeout generoso: el usuario puede tardar en buscar la carpeta.
TIMEOUT_DIALOGO_SEG = 10 * 60

_SCRIPT_DIALOGO = r'''
import sys, json
import tkinter as tk
from tkinter import filedialog

cfg = json.loads(sys.argv[1])

root = tk.Tk()
root.withdraw()
# Sin esto, en Windows el dialogo suele aparecer DETRAS del navegador y
# parece que el boton "no hizo nada".
root.attributes("-topmost", True)
root.update()

if cfg["tipo"] == "carpeta":
    ruta = filedialog.askdirectory(
        title=cfg["titulo"],
        initialdir=cfg["initialdir"] or None,
        mustexist=True,
    )
else:
    ruta = filedialog.askopenfilename(
        title=cfg["titulo"],
        initialdir=cfg["initialdir"] or None,
        filetypes=[
            ("Planillas y CSV", "*.csv *.xlsx *.xls"),
            ("CSV", "*.csv"),
            ("Excel", "*.xlsx *.xls"),
            ("Todos los archivos", "*.*"),
        ],
    )

root.destroy()
# Se escribe en bytes utf-8 a proposito: las rutas con acentos o enie se
# rompen si se dejan a merced de la codificacion de consola de Windows.
sys.stdout.buffer.write((ruta or "").encode("utf-8"))
'''


def dialogo_disponible() -> bool:
    """¿Se puede abrir el explorador nativo desde acá? Requiere tkinter
    (viene con Python, pero en algunos Linux hay que instalarlo aparte) y un
    entorno gráfico."""
    if importlib.util.find_spec("tkinter") is None:
        return False
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        return False
    return True


def abrir_dialogo(tipo: str, titulo: str, initialdir: str):
    """Abre el explorador nativo y devuelve (ruta_elegida, error).

    ruta_elegida es "" si el usuario canceló."""
    if not dialogo_disponible():
        return None, ("No se puede abrir el explorador de archivos en esta máquina "
                      "(falta tkinter o no hay entorno gráfico). Pegá la ruta a mano.")

    if not _LOCK_DIALOGO.acquire(blocking=False):
        return None, "Ya hay una ventana de selección abierta. Cerrala y probá de nuevo."

    try:
        cfg = json.dumps({"tipo": tipo, "titulo": titulo, "initialdir": initialdir})
        kwargs = {}
        if os.name == "nt":
            # Evita el parpadeo de una consola negra al abrir el diálogo.
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        proc = subprocess.run(
            [sys.executable, "-c", _SCRIPT_DIALOGO, cfg],
            capture_output=True,
            timeout=TIMEOUT_DIALOGO_SEG,
            **kwargs,
        )
    except subprocess.TimeoutExpired:
        return None, "La ventana de selección quedó abierta demasiado tiempo y se canceló."
    except OSError as e:
        return None, f"No se pudo abrir el explorador de archivos: {e}"
    finally:
        _LOCK_DIALOGO.release()

    if proc.returncode != 0:
        detalle = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        motivo = detalle[-1] if detalle else f"código {proc.returncode}"
        return None, f"No se pudo abrir el explorador de archivos: {motivo}"

    return proc.stdout.decode("utf-8", errors="replace").strip(), None


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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _leer_payload_json(self):
        """Devuelve (dict, None) o (None, mensaje de error)."""
        try:
            largo = int(self.headers.get("Content-Length", 0))
            cuerpo = self.rfile.read(largo) if largo else b""
            payload = json.loads(cuerpo.decode("utf-8")) if cuerpo else {}
            if not isinstance(payload, dict):
                raise ValueError("El cuerpo debe ser un objeto JSON")
            return payload, None
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
            return None, f"JSON inválido: {e}"

    def do_GET(self):
        ruta = self.path.split("?", 1)[0]

        rutas_api = {
            "/api/sucursales": "sucursales",
            "/api/clientes": "clientes",
            "/api/seguimiento": "seguimiento",
        }

        if ruta in rutas_api:
            self._responder_csv(ruta_efectiva(rutas_api[ruta]))
            return

        if ruta in ("/api/flota", "/api/tipos"):
            self._responder_csv(ruta_extra(ruta[5:]))
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

        if ruta == "/api/config":
            self._post_config()
            return

        if ruta == "/api/elegir":
            self._post_elegir()
            return

        if ruta == "/api/osrm":
            payload, error = self._leer_payload_json()
            if error:
                self._responder_json(400, {"error": error})
                return
            try:
                self._responder_json(200, consultar_osrm(payload))
            except Exception as e:  # sin red, límite del servidor, etc.
                self._responder_json(502, {"error": f"{type(e).__name__}: {e}"})
            return

        self._responder_error(404, "No encontrado")

    # ── POST /api/config: guardar rutas escritas a mano ──────────────────
    def _post_config(self):
        payload, error = self._leer_payload_json()
        if error:
            self._responder_json(400, {"ok": False, "error": error})
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

            validada, error_ruta = validar_ruta(clave, valor)
            if error_ruta:
                errores[clave] = error_ruta
                continue

            overrides[clave] = str(validada)

        if errores:
            # No guardamos nada de la tanda si alguna ruta pedida es inválida,
            # para no dejar el config a medio actualizar de forma confusa.
            self._responder_json(400, {"ok": False, "errores": errores, **estado_config()})
            return

        self._guardar_y_responder(overrides)

    # ── POST /api/elegir: abrir el explorador nativo ─────────────────────
    def _post_elegir(self):
        payload, error = self._leer_payload_json()
        if error:
            self._responder_json(400, {"ok": False, "error": error})
            return

        clave = payload.get("clave")
        if clave not in CLAVES_OVERRIDE:
            self._responder_json(400, {"ok": False, "error": f"Clave desconocida: {clave}"})
            return

        overrides = cargar_overrides()
        es_carpeta = clave == "carpeta_datos"

        # Arrancamos el explorador parados donde ya está apuntando la config,
        # así el usuario no tiene que navegar desde cero cada vez.
        if es_carpeta:
            inicio = carpeta_efectiva(overrides)
            titulo = "Elegí la carpeta con los archivos de datos"
        else:
            actual = ruta_efectiva(clave)
            inicio = actual.parent
            titulo = f"Elegí el archivo de {ETIQUETA[clave].lower()}"
        initialdir = str(inicio) if inicio.is_dir() else ""

        elegida, error_dialogo = abrir_dialogo(
            "carpeta" if es_carpeta else "archivo", titulo, initialdir
        )
        if error_dialogo:
            self._responder_json(500, {"ok": False, "error": error_dialogo, **estado_config()})
            return

        if not elegida:
            # El usuario cerró la ventana sin elegir: no es un error, no se
            # toca nada de la configuración.
            self._responder_json(200, {"ok": True, "cancelado": True, **estado_config()})
            return

        validada, error_ruta = validar_ruta(clave, elegida)
        if error_ruta:
            self._responder_json(400, {"ok": False, "errores": {clave: error_ruta}, **estado_config()})
            return

        overrides[clave] = str(validada)

        # Si eligió una carpeta nueva, los overrides de archivo que apuntaban
        # a la carpeta vieja dejan de tener sentido: se sueltan para que cada
        # archivo se resuelva dentro de la carpeta recién elegida, siempre que
        # ahí exista uno con el nombre por defecto.
        if es_carpeta:
            for clave_archivo in CLAVES_ARCHIVO:
                if clave_archivo not in overrides:
                    continue
                candidata = validada / NOMBRE_POR_DEFECTO[clave_archivo]
                if candidata.is_file() or buscar_por_patron(clave_archivo, validada) is not None:
                    overrides.pop(clave_archivo, None)

        self._guardar_y_responder(overrides, {"elegida": str(validada)})

    def _guardar_y_responder(self, overrides: dict, extra: dict = None):
        try:
            guardar_overrides(overrides)
        except OSError as e:
            # Ej: no hay permiso de escritura en la carpeta de app_visor.py.
            # Antes esto rompía la conexión sin avisar nada ("no anda" a
            # secas); ahora se ve un mensaje concreto en el visor.
            self._responder_json(
                500,
                {"ok": False, "error": f"No se pudo guardar la configuración en "
                                       f"{ARCHIVO_CONFIG_OVERRIDES}: {e}"},
            )
            return

        self._responder_json(200, {"ok": True, **(extra or {}), **estado_config()})


def abrir_navegador(url: str):
    # Pequeña espera para asegurarnos de que el servidor ya esté escuchando
    # antes de intentar abrir la pestaña.
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()


def main():
    url = f"http://localhost:{PUERTO}/"
    try:
        servidor = ThreadingHTTPServer(("localhost", PUERTO), VisorHandler)
    except OSError as e:
        print(f"No se pudo abrir el puerto {PUERTO}: {e}")
        print("Puede que ya haya otra copia de app_visor.py corriendo. "
              "Cerrala, o cambiá PUERTO arriba en este archivo.")
        return

    estado = estado_config()
    print(f"Visor Paladini corriendo en {url}")
    carpeta = estado["carpeta_datos"]
    origen_carpeta = ("carpeta elegida desde el visor"
                      if carpeta["origen"] == "personalizada" else "valor por defecto")
    print(f"Carpeta de datos: {carpeta['ruta']}  ({origen_carpeta})")
    for clave in CLAVES_ARCHIVO:
        info = estado[clave]
        origen = "ruta personalizada" if info["origen"] == "personalizada" else "valor por defecto"
        marca = "" if info["existe"] else "  [NO SE ENCONTRÓ]"
        print(f"  - {ETIQUETA[clave]}: {info['ruta']}  ({origen}){marca}")

    if estado["dialogo_disponible"]:
        print('Desde el botón "Configuración" del visor podés elegir la carpeta o '
              'cada archivo con el explorador, sin escribir rutas ni editar este archivo.')
    else:
        print("Aviso: no se puede abrir el explorador de archivos en esta máquina "
              "(falta tkinter o no hay entorno gráfico); desde el visor vas a tener "
              "que pegar las rutas a mano.")
    print("Presioná Ctrl+C para detener el servidor.")

    abrir_navegador(url)

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor detenido.")
        servidor.shutdown()


if __name__ == "__main__":
    main()