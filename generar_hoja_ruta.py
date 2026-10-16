#!/usr/bin/env python3
"""
generar_hoja_ruta.py — Genera la hoja de ruta de una sucursal para una fecha.
Paladini S.A. — V1 SIMPLE, independiente del visor.

QUÉ HACE
  1. Toma los pedidos (facturas) de la fecha y la sucursal elegidas.
  2. Junta las facturas de cada cliente en una sola parada (suma de kg).
  3. ASIGNA clientes a camiones sin pasarse de la capacidad de cada uno.
  4. ORDENA las paradas de cada camión (primero la más cercana a la sucursal,
     después siempre la más cercana a la última).
  5. Escribe la hoja de ruta en CSV y JSON.

QUÉ NO HACE (todavía)
  - No usa OSRM: las distancias son EN LÍNEA RECTA (fórmula haversine).
    Los km reales por calle serán mayores.
  - No considera horarios de clientes (PENDIENTE DE DEFINIR).
  - No hace varios viajes por camión: cada camión sale una sola vez.
  - No considera restricciones por zona/tipo de camión.
  - No calcula horas de llegada (falta definir velocidad y tiempo de servicio).

USO
    python generar_hoja_ruta.py                      (pregunta fecha y sucursal)
    python generar_hoja_ruta.py --fecha 2026-07-21 --sucursal "Neuquén" --carpeta C:\\ruta\\a\\datos

  Archivos esperados dentro de --carpeta (o indicalos uno por uno con
  --clientes, --seguimiento, --sucursales, --flota):
    clientes     -> id_cliente, latitude, longitude, sucursal_asignada
    seguimiento  -> id_cliente, Cliente, Factura, InicioTransp, KGTotal
    sucursales   -> sucursal, latitude, longitude
    flota        -> sucursal, patente, tipo            (un camión por fila)
    tipos        -> tipo, capacidad_planificacion_kg, tanque_litros, consumo_l_100km,
                    autonomia_km, ...                  (un tipo Z1..Z7 por fila)
"""

import argparse
import csv
import json
import math
import sys
import unicodedata
from datetime import date, datetime
from pathlib import Path

# Nombres de archivo por defecto y textos para reconocerlos dentro de la carpeta
PATRONES = {
    "clientes": ["cliente", "geolocaliz"],
    "seguimiento": ["seguimiento", "entrega", "logistic"],
    "sucursales": ["sucursal"],
    "flota": ["flota"],
    "tipos": ["tipos"],
}

# Nombres de columna aceptados (sin importar mayúsculas ni acentos)
ALIAS = {
    "id_cliente": ["id_cliente"],
    "lat": ["latitude", "lat", "latitud"],
    "lon": ["longitude", "lon", "lng", "longitud"],
    "sucursal_cli": ["sucursal_asignada"],
    "cliente": ["cliente"],
    "factura": ["factura"],
    "fecha": ["iniciotransp"],
    "kg": ["kgtotal", "kg"],
    "sucursal": ["sucursal", "nombre_sucursal", "nombre_local", "nombre"],
    "patente": ["patente"],
    "tipo": ["tipo"],
    "capacidad": ["capacidad_kg", "capacidad"],
    "cap_plan": ["capacidad_planificacion_kg"],
    "tanque": ["tanque_litros"],
    "consumo": ["consumo_l_100km"],
    "autonomia": ["autonomia_km"],
}


# ── Utilidades ─────────────────────────────────────────────────────────
def norm(texto):
    """Minúsculas y sin acentos, para comparar textos."""
    t = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in t if not unicodedata.combining(c)).strip().lower()


def clave_sucursal(texto):
    """'Paladini S.A. - Sucursal Neuquén' y 'Neuquén' -> 'neuquen'."""
    t = norm(texto).replace("paladini s.a.", " ").replace("sucursal", " ")
    return " ".join(t.replace("-", " ").split())


def misma_sucursal(a, b):
    """True si dos nombres de sucursal se refieren a la misma (tolera el
    prefijo 'Paladini S.A. - Sucursal' y diferencias de tildes/mayúsculas)."""
    ka, kb = clave_sucursal(a), clave_sucursal(b)
    return bool(ka) and bool(kb) and (ka == kb or ka in kb or kb in ka)


def numero(raw):
    """'1.234,5' o '866,93' o '12.5' -> float. Vacío/ilegible -> None."""
    s = str(raw).strip().replace(" ", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def leer_csv(ruta):
    """Lee un CSV detectando codificación y separador. Devuelve lista de dicts
    con las claves de columna ya normalizadas (minúsculas, sin acentos)."""
    texto = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            texto = Path(ruta).read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    primera = texto.splitlines()[0] if texto else ""
    sep = ";" if primera.count(";") >= primera.count(",") else ","
    lector = csv.DictReader(texto.splitlines(), delimiter=sep)
    filas = []
    for f in lector:
        filas.append({norm(k): (v or "").strip() for k, v in f.items() if k})
    return filas


def col(fila, clave, obligatoria=True, archivo=""):
    """Devuelve el valor de la columna cuyo nombre esté entre los alias."""
    for a in ALIAS[clave]:
        if a in fila:
            return fila[a]
    if obligatoria:
        raise SystemExit(f"ERROR: en {archivo} falta la columna "
                         f"'{ALIAS[clave][0]}'. Columnas: {list(fila)}")
    return None


def parsear_fecha(texto):
    """Acepta 2026-07-21, 21/07/2026 o '21/7/2026 00:00'."""
    t = texto.strip().split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except ValueError:
            continue
    return None


def haversine_km(a, b):
    """Distancia en línea recta entre dos (lat, lon), en km."""
    r = 6371.0088
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(h))


def encontrar_archivo(carpeta, clave):
    """Busca en la carpeta UN archivo cuyo nombre contenga algún patrón."""
    candidatos = [p for p in Path(carpeta).iterdir()
                  if p.is_file() and p.suffix.lower() == ".csv"
                  and any(pat in norm(p.stem) for pat in PATRONES[clave])]
    if len(candidatos) == 1:
        return candidatos[0]
    if not candidatos:
        raise SystemExit(f"ERROR: no encontré el archivo de {clave} en "
                         f"{carpeta}. Indicalo con --{clave}.")
    raise SystemExit(f"ERROR: hay varios archivos posibles para {clave}: "
                     f"{[p.name for p in candidatos]}. Indicá uno con --{clave}.")


# ── Carga de datos ─────────────────────────────────────────────────────
def cargar_sucursal(ruta, nombre):
    """Busca la sucursal por nombre (exacto o parcial, ej. 'Neuquén')."""
    filas = leer_csv(ruta)
    nombres = [col(f, "sucursal", archivo=Path(ruta).name) for f in filas]
    exactas = [i for i, n in enumerate(nombres)
               if clave_sucursal(n) == clave_sucursal(nombre)]
    parciales = [i for i, n in enumerate(nombres) if misma_sucursal(n, nombre)]
    elegidas = exactas or parciales
    if len(elegidas) != 1:
        motivo = "no encontrada" if not elegidas else "ambigua"
        raise SystemExit(f"ERROR: sucursal '{nombre}' {motivo}. "
                         f"Disponibles: {nombres}")
    f = filas[elegidas[0]]
    lat, lon = numero(col(f, "lat")), numero(col(f, "lon"))
    if lat is None or lon is None:
        raise SystemExit(f"ERROR: la sucursal '{nombres[elegidas[0]]}' no tiene coordenadas.")
    return nombres[elegidas[0]], (lat, lon)


def cargar_tipos(ruta):
    """tipos_camion.csv -> {tipo: datos}. La capacidad que usa el motor es
    capacidad_planificacion_kg (editable: por defecto, el mínimo del rango)."""
    tipos = {}
    for f in leer_csv(ruta):
        t = norm(col(f, "tipo", archivo="tipos_camion"))
        cap = numero(col(f, "cap_plan", archivo="tipos_camion"))
        if cap is None or cap <= 0:
            raise SystemExit(f"ERROR: el tipo '{t}' no tiene capacidad_planificacion_kg válida.")
        tipos[t] = {"capacidad_kg": cap,
                    "tanque_litros": numero(col(f, "tanque", False) or ""),
                    "consumo_l_100km": numero(col(f, "consumo", False) or ""),
                    "autonomia_km": numero(col(f, "autonomia", False) or "")}
    if not tipos:
        raise SystemExit("ERROR: el archivo de tipos de camión está vacío.")
    return tipos


def cargar_flota(ruta_flota, ruta_tipos, sucursal):
    """flota.csv (sucursal, patente, tipo) + tipos_camion.csv -> camiones."""
    tipos = cargar_tipos(ruta_tipos)
    camiones = []
    for f in leer_csv(ruta_flota):
        if not misma_sucursal(col(f, "sucursal", archivo="flota"), sucursal):
            continue
        patente = col(f, "patente", archivo="flota")
        tipo = col(f, "tipo", archivo="flota")
        datos = tipos.get(norm(tipo))
        if datos is None:
            raise SystemExit(f"ERROR: el camión {patente} tiene el tipo '{tipo}', que no "
                             f"está en tipos_camion. Tipos válidos: {sorted(tipos)}")
        camiones.append({"patente": patente, "tipo": tipo.upper(), **datos})
    if not camiones:
        raise SystemExit(f"ERROR: no hay camiones cargados para '{sucursal}' en el "
                         f"archivo de flota.")
    return camiones


def cargar_paradas(ruta_cli, ruta_seg, sucursal, fecha):
    """Devuelve (paradas, no_asignados_previos, avisos)."""
    # Clientes: coordenadas y sucursal asignada
    geo = {}
    for f in leer_csv(ruta_cli):
        idc = col(f, "id_cliente", archivo="clientes")
        geo.setdefault(idc, {
            "lat": numero(col(f, "lat", archivo="clientes")),
            "lon": numero(col(f, "lon", archivo="clientes")),
            "sucursal": col(f, "sucursal_cli", False) or "",
        })

    # Seguimiento: facturas de la fecha, agrupadas por cliente
    por_cliente = {}
    for f in leer_csv(ruta_seg):
        fe = parsear_fecha(col(f, "fecha", archivo="seguimiento"))
        if fe != fecha:
            continue
        idc = col(f, "id_cliente", archivo="seguimiento")
        p = por_cliente.setdefault(idc, {"nombre": col(f, "cliente", False) or "",
                                         "facturas": [], "kg": 0.0})
        p["facturas"].append(col(f, "factura", False) or "")
        p["kg"] += numero(col(f, "kg", archivo="seguimiento")) or 0.0

    paradas, excluidos, avisos = [], [], []
    otra_sucursal = sin_geo = 0
    for idc, p in por_cliente.items():
        g = geo.get(idc)
        if g is None:
            sin_geo += 1
            excluidos.append({"id_cliente": idc, "cliente": p["nombre"],
                              "kg": round(p["kg"], 2),
                              "motivo": "cliente sin geolocalización"})
            continue
        if not misma_sucursal(g["sucursal"], sucursal):
            otra_sucursal += 1
            continue
        if g["lat"] is None or g["lon"] is None:
            excluidos.append({"id_cliente": idc, "cliente": p["nombre"],
                              "kg": round(p["kg"], 2),
                              "motivo": "cliente sin coordenadas válidas"})
            continue
        paradas.append({"id_cliente": idc, "cliente": p["nombre"],
                        "facturas": p["facturas"], "kg": p["kg"],
                        "coord": (g["lat"], g["lon"])})

    if otra_sucursal:
        avisos.append(f"{otra_sucursal} cliente(s) del día pertenecen a otra sucursal y se omitieron.")
    if sin_geo:
        avisos.append(f"{sin_geo} cliente(s) del día no están en el archivo de "
                      f"clientes (no se sabe su sucursal): revisar.")
    return paradas, excluidos, avisos


# ── Asignación y orden ─────────────────────────────────────────────────
def armar_grupos(paradas, camiones, depot):
    """Llena camiones (de mayor a menor capacidad) con clientes cercanos entre sí.
    Devuelve (grupos, sin_asignar)."""
    pendientes = list(paradas)
    grupos = []
    for cam in sorted(camiones, key=lambda c: -c["capacidad_kg"]):
        if not pendientes:
            break
        libre = cam["capacidad_kg"]
        cand = [p for p in pendientes if p["kg"] <= libre]
        if not cand:
            continue
        # semilla: el cliente más lejano de la sucursal que entre
        semilla = max(cand, key=lambda p: haversine_km(depot, p["coord"]))
        grupo = [semilla]
        pendientes.remove(semilla)
        libre -= semilla["kg"]
        while True:
            cand = [p for p in pendientes if p["kg"] <= libre]
            if not cand:
                break
            cy = sum(p["coord"][0] for p in grupo) / len(grupo)
            cx = sum(p["coord"][1] for p in grupo) / len(grupo)
            sig = min(cand, key=lambda p: haversine_km((cy, cx), p["coord"]))
            grupo.append(sig)
            pendientes.remove(sig)
            libre -= sig["kg"]
        grupos.append(grupo)
    return grupos, pendientes


def asignar_camion_justo(grupos, camiones):
    """Cada grupo va al camión más chico donde entra (grupos más pesados primero),
    para no usar un camión grande para poca carga."""
    libres = sorted(camiones, key=lambda c: c["capacidad_kg"])
    resultado = []
    for grupo in sorted(grupos, key=lambda g: -sum(p["kg"] for p in g)):
        kg = sum(p["kg"] for p in grupo)
        cam = next(c for c in libres if c["capacidad_kg"] >= kg)
        libres.remove(cam)
        resultado.append((cam, grupo))
    return resultado


def ordenar_paradas(grupo, depot):
    """Vecino más cercano, empezando desde la sucursal."""
    resto, orden, actual = list(grupo), [], depot
    while resto:
        sig = min(resto, key=lambda p: haversine_km(actual, p["coord"]))
        orden.append(sig)
        resto.remove(sig)
        actual = sig["coord"]
    return orden


def construir_hoja(sucursal, depot, fecha, paradas, camiones, excluidos, avisos):
    grupos, sin_asignar = armar_grupos(paradas, camiones, depot)
    for p in sin_asignar:
        motivo = ("pedido más pesado que cualquier camión de la sucursal"
                  if p["kg"] > max(c["capacidad_kg"] for c in camiones)
                  else "no quedó capacidad de flota (V1: un solo viaje por camión)")
        excluidos.append({"id_cliente": p["id_cliente"], "cliente": p["cliente"],
                          "kg": round(p["kg"], 2), "motivo": motivo})

    rutas = []
    for cam, grupo in asignar_camion_justo(grupos, camiones):
        orden = ordenar_paradas(grupo, depot)
        actual, acum, stops = depot, 0.0, []
        for i, p in enumerate(orden, 1):
            tramo = haversine_km(actual, p["coord"])
            acum += tramo
            stops.append({"orden": i, "id_cliente": p["id_cliente"],
                          "cliente": p["cliente"], "facturas": p["facturas"],
                          "kg": round(p["kg"], 2),
                          "latitude": p["coord"][0], "longitude": p["coord"][1],
                          "km_tramo": round(tramo, 2), "km_acumulado": round(acum, 2)})
            actual = p["coord"]
        vuelta = haversine_km(actual, depot)
        kg = sum(s["kg"] for s in stops)
        total = acum + vuelta
        if cam.get("autonomia_km") and total > cam["autonomia_km"]:
            avisos.append(f"{cam['patente']}: la ruta (línea recta) supera la autonomía "
                          f"estimada ({round(total)} km > {round(cam['autonomia_km'])} km).")
        rutas.append({"patente": cam["patente"], "tipo": cam["tipo"],
                      "capacidad_kg": cam["capacidad_kg"],
                      "autonomia_km": cam.get("autonomia_km"),
                      "kg_cargados": round(kg, 2),
                      "ocupacion_pct": round(100 * kg / cam["capacidad_kg"], 1),
                      "paradas": stops, "km_vuelta_sucursal": round(vuelta, 2),
                      "km_total": round(acum + vuelta, 2)})

    return {
        "fecha": fecha.isoformat(),
        "sucursal": sucursal,
        "sucursal_coord": {"latitude": depot[0], "longitude": depot[1]},
        "aviso_metodo": "Km en línea recta (sin OSRM), sin horarios de clientes, "
                        "un viaje por camión, sin restricciones por zona.",
        "resumen": {"camiones_usados": len(rutas),
                    "camiones_disponibles": len(camiones),
                    "clientes_asignados": sum(len(r["paradas"]) for r in rutas),
                    "clientes_sin_asignar": len(excluidos),
                    "km_total_linea_recta": round(sum(r["km_total"] for r in rutas), 2),
                    "kg_total": round(sum(r["kg_cargados"] for r in rutas), 2)},
        "rutas": rutas,
        "sin_asignar": excluidos,
        "avisos": avisos,
    }


# ── Salida ─────────────────────────────────────────────────────────────
def escribir_salidas(hoja, carpeta_salida):
    carpeta_salida.mkdir(parents=True, exist_ok=True)
    base = f"hoja_ruta_{clave_sucursal(hoja['sucursal']).replace(' ', '_')}_{hoja['fecha']}"
    ruta_json = carpeta_salida / f"{base}.json"
    ruta_csv = carpeta_salida / f"{base}.csv"

    ruta_json.write_text(json.dumps(hoja, ensure_ascii=False, indent=2), encoding="utf-8")

    with ruta_csv.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["fecha", "sucursal", "patente", "tipo", "capacidad_kg", "orden",
                    "id_cliente", "cliente", "facturas", "kg", "km_tramo", "km_acumulado"])
        for r in hoja["rutas"]:
            for s in r["paradas"]:
                w.writerow([hoja["fecha"], hoja["sucursal"], r["patente"], r["tipo"],
                            r["capacidad_kg"], s["orden"], s["id_cliente"], s["cliente"],
                            " ".join(s["facturas"]), str(s["kg"]).replace(".", ","),
                            str(s["km_tramo"]).replace(".", ","),
                            str(s["km_acumulado"]).replace(".", ",")])
            w.writerow([hoja["fecha"], hoja["sucursal"], r["patente"], r["tipo"],
                        r["capacidad_kg"], "VUELTA", "", "SUCURSAL", "", "",
                        str(r["km_vuelta_sucursal"]).replace(".", ","),
                        str(r["km_total"]).replace(".", ",")])
    return ruta_json, ruta_csv


def imprimir(hoja):
    r = hoja["resumen"]
    print(f"\nHOJA DE RUTA — {hoja['sucursal']} — {hoja['fecha']}")
    print(f"Camiones usados: {r['camiones_usados']}/{r['camiones_disponibles']}  |  "
          f"Clientes asignados: {r['clientes_asignados']}  |  "
          f"Km (línea recta): {r['km_total_linea_recta']}  |  Kg: {r['kg_total']}")
    for ruta in hoja["rutas"]:
        print(f"\n  {ruta['patente']} {ruta['tipo']}  "
              f"{ruta['kg_cargados']}/{ruta['capacidad_kg']} kg "
              f"({ruta['ocupacion_pct']}%)  {ruta['km_total']} km")
        for s in ruta["paradas"]:
            print(f"    {s['orden']:>2}. {s['id_cliente']:<8} {s['cliente'][:32]:<32} "
                  f"{s['kg']:>9.2f} kg  +{s['km_tramo']} km")
        print(f"     vuelta a sucursal +{ruta['km_vuelta_sucursal']} km")
    if hoja["sin_asignar"]:
        print("\n  SIN ASIGNAR:")
        for s in hoja["sin_asignar"]:
            print(f"    {s['id_cliente']:<8} {s['cliente'][:32]:<32} {s['kg']} kg — {s['motivo']}")
    for a in hoja["avisos"]:
        print(f"  AVISO: {a}")


def main():
    ap = argparse.ArgumentParser(description="Genera la hoja de ruta de una sucursal y fecha.")
    ap.add_argument("--fecha", help="AAAA-MM-DD o DD/MM/AAAA (si se omite, la pregunta)")
    ap.add_argument("--sucursal", help="nombre o parte del nombre (si se omite, la pregunta)")
    ap.add_argument("--carpeta", help="carpeta con los CSV (por defecto: ./data junto al script, o la carpeta del script)")
    ap.add_argument("--clientes")
    ap.add_argument("--seguimiento")
    ap.add_argument("--sucursales")
    ap.add_argument("--flota")
    ap.add_argument("--tipos", help="CSV de tipos de camión (tipos_camion.csv)")
    ap.add_argument("--salida", default="salida", help="carpeta de resultados")
    a = ap.parse_args()

    # Carpeta por defecto: "data" junto al script si existe; si no, la del script
    if not a.carpeta and not all(getattr(a, k) for k in PATRONES):
        aqui = Path(__file__).resolve().parent
        a.carpeta = str(aqui / "data" if (aqui / "data").is_dir() else aqui)
        print(f"Carpeta de datos: {a.carpeta}")

    rutas = {}
    for clave in PATRONES:
        explicita = getattr(a, clave)
        if explicita:
            rutas[clave] = Path(explicita)
        else:
            rutas[clave] = encontrar_archivo(a.carpeta, clave)
        if not rutas[clave].is_file():
            sys.exit(f"ERROR: no existe el archivo {rutas[clave]}")

    # Si faltan fecha o sucursal, se preguntan
    if not a.sucursal:
        nombres = [col(f, "sucursal", archivo="sucursales")
                   for f in leer_csv(rutas["sucursales"])]
        print("\nSucursales:")
        for i, n in enumerate(nombres, 1):
            print(f"  {i}. {n}")
        resp = input("Elegí sucursal (número o nombre): ").strip()
        a.sucursal = nombres[int(resp) - 1] if resp.isdigit() and 1 <= int(resp) <= len(nombres) else resp
    if not a.fecha:
        a.fecha = input("Fecha (AAAA-MM-DD o DD/MM/AAAA): ").strip()

    fecha = parsear_fecha(a.fecha)
    if fecha is None:
        sys.exit("ERROR: fecha inválida. Usá AAAA-MM-DD o DD/MM/AAAA.")

    sucursal, depot = cargar_sucursal(rutas["sucursales"], a.sucursal)
    camiones = cargar_flota(rutas["flota"], rutas["tipos"], sucursal)
    paradas, excluidos, avisos = cargar_paradas(rutas["clientes"], rutas["seguimiento"],
                                                sucursal, fecha)
    if not paradas:
        sys.exit(f"No hay pedidos para {sucursal} el {fecha}.")

    hoja = construir_hoja(sucursal, depot, fecha, paradas, camiones, excluidos, avisos)
    imprimir(hoja)
    rj, rc = escribir_salidas(hoja, Path(a.salida))
    print(f"\nArchivos generados:\n  {rc}\n  {rj}")


if __name__ == "__main__":
    main()