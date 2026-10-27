"use strict";
/*
 * ruta_logica.js — Lógica de la app Hoja de ruta, SIN interfaz (no toca el DOM).
 *
 * Etapa 1: carga y parseo de datos + armado de los clientes facturados.
 * Las funciones están copiadas de visor.html (mismos nombres y comportamiento)
 * para que las etapas siguientes puedan copiar el algoritmo tal cual.
 * Requiere PapaParse cargado antes.
 */

// ── Utilidades de texto y números (copiadas de visor.html) ──────────
const norm = t => String(t == null ? "" : t).normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim().toLowerCase();
const ksuc = t => norm(t).replace("paladini s.a.", " ").replace("sucursal", " ").replace(/-/g, " ").replace(/\s+/g, " ").trim();
const mismaSuc = (a, b) => { const x = ksuc(a), y = ksuc(b); return !!x && !!y && (x === y || x.includes(y) || y.includes(x)); };
const val = (row, k) => { const f = Object.keys(row).find(h => norm(h) === k); return f ? row[f] : ""; };
const f1 = n => (Math.round(n * 10) / 10).toLocaleString("es-AR");
const pad = n => String(n).padStart(2, "0");
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function logDetectDelimiter(sampleText) {
  const firstLine = (sampleText.split(/\r?\n/)[0] || "");
  const semi = (firstLine.match(/;/g) || []).length;
  const comma = (firstLine.match(/,/g) || []).length;
  return semi > comma ? ";" : ",";
}

function logNormalizeHeaders(rows) {
  return rows.map(row => {
    const n = {};
    for (const [k, v] of Object.entries(row)) n[k.trim()] = typeof v === "string" ? v.trim() : v;
    return n;
  });
}

// "866,93" -> 866.93 ; "1.234,56" -> 1234.56 ; "35.5" -> 35.5
function logParseNumber(raw) {
  if (raw == null || raw === "") return 0;
  let s = String(raw).trim();
  if (s.includes(",")) s = s.replace(/\./g, "").replace(",", ".");
  const n = parseFloat(s);
  return isNaN(n) ? 0 : n;
}

// "21/7/2026 00:00" (d/m/aaaa H:mm) -> Date | null
function logParseFecha(raw) {
  if (!raw) return null;
  const m = String(raw).trim().match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})(?:\s+(\d{1,2}):(\d{2}))?/);
  if (!m) return null;
  const [, d, mo, y, h = "0", mi = "0"] = m;
  return new Date(+y, +mo - 1, +d, +h, +mi);
}

function parsearCSV(text) {
  text = text.replace(/^\uFEFF/, "");
  const r = Papa.parse(text, { header: true, skipEmptyLines: true, delimiter: logDetectDelimiter(text) });
  return logNormalizeHeaders(r.data);
}

function exigirColumnas(rows, cols, nombre) {
  if (!rows.length) throw new Error(`${nombre}: el archivo está vacío`);
  const faltan = cols.filter(c => !(c in rows[0]));
  if (faltan.length) throw new Error(`${nombre}: faltan columnas ${faltan.join(", ")}`);
}

// ── Clientes geolocalizados ─────────────────────────────────────────
// Devuelve Map id_cliente -> { id, co:[lat, lon], dir, suc }.
// Igual que el visor: se toman todas las filas con coordenadas válidas
// (SUCCESS o FAILED) y, si un id se repite, gana el primer registro.
function procesarGeo(rows) {
  exigirColumnas(rows, ["id_cliente", "latitude", "longitude"], "Clientes");
  const geo = new Map();
  rows.forEach(r => {
    const lat = parseFloat(r.latitude), lon = parseFloat(r.longitude);
    const id = String(r.id_cliente || "").trim();
    if (isNaN(lat) || isNaN(lon) || !id || geo.has(id)) return;
    geo.set(id, { id, co: [lat, lon], dir: r.original_address || "", suc: r.sucursal_asignada || "" });
  });
  if (!geo.size) throw new Error("Clientes: ninguna fila con coordenadas válidas");
  return geo;
}

// ── Sucursales (con alias de columnas, como el visor) ───────────────
const SUCURSAL_ALIASES = {
  sucursal: ["sucursal", "nombre_local", "nombre_sucursal", "nombre"],
  direccion: ["direccion", "dirección", "domicilio"],
  latitude: ["latitude", "lat", "latitud"],
  longitude: ["longitude", "lon", "lng", "longitud"],
};

function logFindAlias(row, aliases) {
  const keys = Object.keys(row);
  for (const alias of aliases) {
    const found = keys.find(k => k.toLowerCase() === alias.toLowerCase());
    if (found) return found;
  }
  return null;
}

function procesarSucursales(rows) {
  if (!rows.length) throw new Error("Sucursales: el archivo está vacío");
  const cSuc = logFindAlias(rows[0], SUCURSAL_ALIASES.sucursal);
  const cDir = logFindAlias(rows[0], SUCURSAL_ALIASES.direccion);
  const cLat = logFindAlias(rows[0], SUCURSAL_ALIASES.latitude);
  const cLon = logFindAlias(rows[0], SUCURSAL_ALIASES.longitude);
  if (!cSuc || !cLat || !cLon) throw new Error("Sucursales: faltan columnas de nombre, latitud o longitud");
  const clean = [];
  rows.forEach(r => {
    const lat = parseFloat(r[cLat]), lon = parseFloat(r[cLon]);
    if (isNaN(lat) || isNaN(lon)) return;
    clean.push({ sucursal: r[cSuc] || "Sucursal", direccion: cDir ? (r[cDir] || "") : "", latitude: lat, longitude: lon });
  });
  if (!clean.length) throw new Error("Sucursales: ninguna fila con coordenadas válidas");
  return clean;
}

// ── Seguimiento / facturación ───────────────────────────────────────
function procesarSeguimiento(rows) {
  exigirColumnas(rows, ["id_cliente", "InicioTransp", "KGTotal"], "Seguimiento");
  return rows.filter(r => r.id_cliente);
}

// Fechas con pedidos, de la más reciente a la más antigua: [{ key:"2026-07-27", label:"27/07/2026" }]
function fechasDisponibles(segRows) {
  const fechas = new Map();
  segRows.forEach(r => { const d = logParseFecha(r.InicioTransp); if (d) fechas.set(iso(d), d); });
  return [...fechas.keys()].sort().reverse().map(k => {
    const d = fechas.get(k);
    return { key: k, label: `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()}` };
  });
}

// Facturación de un día agrupada por cliente (kg sumados, facturas juntas).
// Igual que generar() del visor: Map id_cliente -> { id, nombre, kg, fact:[] }.
function facturacionDelDia(segRows, fechaKey) {
  const por = new Map();
  segRows.forEach(r => {
    const d = logParseFecha(r.InicioTransp);
    if (!d || iso(d) !== fechaKey) return;
    const id = String(r.id_cliente).trim();
    const p = por.get(id) || { id, nombre: r.Cliente || "", kg: 0, fact: [] };
    p.kg += logParseNumber(r.KGTotal);
    p.fact.push(r.Factura);
    por.set(id, p);
  });
  return por;
}

// Cruza la facturación del día con la geolocalización y filtra por sucursal.
// Devuelve las paradas con coordenadas y los conteos de clientes omitidos.
function clientesFacturados(facturacion, geo, sucursal) {
  const paradas = [];
  let otra = 0, sinGeo = 0;
  facturacion.forEach(p => {
    const g = geo.get(p.id);
    if (!g) sinGeo++;
    else if (!mismaSuc(g.suc, sucursal.sucursal)) otra++;
    else paradas.push({ ...p, co: g.co, dir: g.dir });
  });
  return { paradas, otra, sinGeo };
}