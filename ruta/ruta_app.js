"use strict";
/*
 * ruta_app.js — Interfaz de la app Hoja de ruta.
 * Carga de datos, clientes editables en el mapa, camiones, generación de la
 * hoja de ruta (algoritmo copiado en ruta_algoritmo.js), métricas,
 * advertencias y exportación con confirmación.
 * Depende de ruta_logica.js, ruta_algoritmo.js, Leaflet y PapaParse.
 */
(function () {
  const $ = id => document.getElementById(id);
  const esc = t => String(t == null ? "" : t).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const VERDE = "#22863a", GRIS = "#8a8d91", ROJO = "#dc2626";
  // Regla "cliente demasiado lejos": solo ese cliente se marca en rojo (y se avisa arriba).
  const LEJOS_FACTOR = 1.5;      // distancia a la sucursal > 1,5 x el promedio de los demás clientes de la ruta
  const LEJOS_MIN_PARADAS = 3;   // solo se evalúa en rutas con al menos 3 paradas
  const LEJOS_MIN_EXTRA_KM = 30; // y si además supera el promedio por al menos 30 km
  const COLORES = ["#2563eb", "#9333ea", "#ea580c", "#0891b2", "#ca8a04", "#be185d", "#4f46e5", "#0d9488"]; // un color por camión
  const S = {
    geo: null, suc: null, seg: null, flota: null, tipos: null, ventanas: null, errFlota: "",
    map: null, capaCli: null, capaRuta: null,
    cache: new Map(),   // "sucursal|fecha" -> clientes con sus ediciones (se conservan al cambiar de vista)
    ctx: null, off: new Set(), hoja: null, avisos: [], desact: false, ocupado: false,
  };

  // ── Avisos ────────────────────────────────────────────────────────
  let tToast;
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg; t.classList.add("show");
    clearTimeout(tToast); tToast = setTimeout(() => t.classList.remove("show"), 3500);
  }
  function banner(msgs) {
    const b = $("banner");
    b.innerHTML = msgs.map(m => `<span>${m}</span>`).join("");   // los mensajes ya vienen escapados
    b.classList.toggle("visible", msgs.length > 0);
  }
  function vacio(msg) {
    $("empty-texto").textContent = msg || "";
    $("empty").classList.toggle("visible", !!msg);
  }
  function mostrarBanner() {
    const x = S.ctx, base = [];
    if (x && x.otra) base.push(`${x.otra} cliente(s) del día pertenecen a otra sucursal y no se muestran.`);
    if (x && x.sinGeo) base.push(`${x.sinGeo} cliente(s) del día no están en el archivo de clientes: revisar.`);
    banner([...base, ...S.avisos]);
  }

  // ── Carga de datos ────────────────────────────────────────────────
  async function pedir(url, nombre) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`${nombre}: ${await r.text()}`);
    return r.text();
  }

  async function cargarFlota() {
    S.flota = S.tipos = S.ventanas = null; S.errFlota = "";
    try {
      const [t, f] = await Promise.all([pedir("/api/tipos", "Tipos de camión"), pedir("/api/flota", "Flota")]);
      const tipos = new Map();
      parsearCSV(t).forEach(r => tipos.set(norm(val(r, "tipo")), { nombre: val(r, "tipo"), row: r }));
      const flota = parsearCSV(f);
      if (!flota.length) throw new Error("Flota: el archivo está vacío");
      S.tipos = tipos; S.flota = flota;
    } catch (e) { S.errFlota = e.message || String(e); }
    try {   // horarios de entrega de clientes: opcional (solo generan aviso)
      const r = await fetch("/api/horarios", { cache: "no-store" });
      if (r.ok) {
        const m = new Map();
        parsearCSV(await r.text()).forEach(f => {
          const id = String(val(f, "id_cliente")).trim(), vd = RA.aMin(val(f, "desde")), vh = RA.aMin(val(f, "hasta"));
          if (id && (vd != null || vh != null)) m.set(id, { vd, vh });
        });
        S.ventanas = m;
      }
    } catch (e) { /* sin horarios */ }
  }

  async function cargarDatos() {
    const defs = [
      ["Sucursales", "/api/sucursales", t => procesarSucursales(parsearCSV(t)), "suc"],
      ["Clientes", "/api/clientes", t => procesarGeo(parsearCSV(t)), "geo"],
      ["Seguimiento", "/api/seguimiento", t => procesarSeguimiento(parsearCSV(t)), "seg"],
    ];
    const [res] = await Promise.all([
      Promise.allSettled(defs.map(async ([nombre, url, parsear, clave]) => { S[clave] = parsear(await pedir(url, nombre)); })),
      cargarFlota(),
    ]);
    const errores = res.filter(r => r.status === "rejected").map(r => r.reason.message || String(r.reason));
    const dot = $("datos-dot"), txt = $("datos-texto");
    if (errores.length) {
      dot.className = "dot error";
      txt.innerHTML = errores.map(e => `<div class="linea-error">${esc(e)}</div>`).join("");
      return false;
    }
    dot.className = S.flota ? "dot ok" : "dot error";
    txt.innerHTML = `<div>${S.suc.length} sucursales</div><div>${S.geo.size} clientes geolocalizados</div><div>${S.seg.length} filas de seguimiento</div>`
      + (S.flota ? `<div>${S.flota.length} camiones en la flota</div>` : `<div class="linea-error">${esc(S.errFlota)}</div>`)
      + (S.ventanas ? `<div>${S.ventanas.size} clientes con horario de entrega</div>` : "");
    return true;
  }

  // ── Clientes de la sucursal y fecha elegidas ──────────────────────
  // Facturados: incluidos, kilos de la facturación (editables).
  // Resto de los geolocalizados de la sucursal: excluidos, sin kilos.
  function contexto() {
    const suc = S.suc[+$("sel-sucursal").value], fecha = $("sel-fecha").value;
    if (!suc || !fecha) return null;
    const key = suc.sucursal + "|" + fecha;
    if (!S.cache.has(key)) {
      const { paradas, otra, sinGeo } = clientesFacturados(facturacionDelDia(S.seg, fecha), S.geo, suc);
      const m = new Map();
      paradas.forEach(p => m.set(p.id, { id: p.id, nombre: p.nombre, dir: p.dir, co: p.co, kg: p.kg, fact: p.fact, facturado: true, incluido: true, avisos: [], parada: null, marker: null }));
      S.geo.forEach(g => {
        if (!m.has(g.id) && mismaSuc(g.suc, suc.sucursal))
          m.set(g.id, { id: g.id, nombre: "", dir: g.dir, co: g.co, kg: 0, fact: [], facturado: false, incluido: false, avisos: [], parada: null, marker: null });
      });
      S.cache.set(key, { key, suc, fecha, clientes: m, otra, sinGeo });
    }
    return S.cache.get(key);
  }

  const nombreDe = c => c.nombre || "Cliente " + c.id;

  // ── Mapa ──────────────────────────────────────────────────────────
  function initMap() {
    S.map = L.map("map", { zoomControl: true }).setView([-38.95, -68.06], 8);
    L.tileLayer("https://maps.wikimedia.org/osm-intl/{z}/{x}/{y}.png", { attribution: "© OpenStreetMap", maxZoom: 19 }).addTo(S.map);
    S.capaRuta = L.layerGroup().addTo(S.map);
    S.capaCli = L.layerGroup().addTo(S.map);
  }

  const pin = (color, aten, alerta, num) => L.divIcon({
    className: aten ? "pin pin-aten" : "pin",
    html: `<svg class="pin-svg" width="26" height="34" viewBox="0 0 26 34"><path d="M13 1C6.4 1 1 6.3 1 12.8c0 8.6 12 20.2 12 20.2s12-11.6 12-20.2C25 6.3 19.6 1 13 1z" fill="${color}" stroke="#fff" stroke-width="1.5"/>${num != null ? `<circle cx="13" cy="12.5" r="8.6" fill="#fff"/><text x="13" y="16.5" text-anchor="middle" font-family="system-ui,Segoe UI,sans-serif" font-size="${String(num).length > 2 ? 8 : String(num).length > 1 ? 10 : 12}" font-weight="700" fill="${color}">${num}</text>` : `<circle cx="13" cy="12.5" r="4.2" fill="#fff"/>`}</svg>${alerta ? '<span class="pin-alerta"></span>' : ""}`,
    iconSize: [26, 34], iconAnchor: [13, 33], tooltipAnchor: [0, -30],
  });

  // Estado visual. Advertencia y exclusión son independientes:
  //  incluido sin advertencia → verde · incluido con advertencia → rojo
  //  con hoja generada, el punto muestra el número de parada dentro de su camión
  //  facturado excluido → atenuado (con punto rojo si además tiene advertencia)
  //  sin facturación no incluido → gris
  function icono(c) {
    const al = c.avisos.length > 0;
    if (c.incluido) return pin(al ? ROJO : VERDE, false, false, c.parada ? c.parada.orden : null);
    return pin(c.facturado ? VERDE : GRIS, c.facturado, al);
  }

  function popup(c) {
    const el = document.createElement("div");
    el.className = "pop";
    el.innerHTML = `<div class="pop-nombre">${esc(nombreDe(c))}</div><div class="pop-dir">${esc(c.dir || "Sin dirección")}</div>`
      + (c.parada ? `<div class="pop-dato">Camión ${esc(c.parada.pat)} · parada ${c.parada.orden}</div>` : "")
      + (c.avisos.length ? `<div class="pop-aviso">${c.avisos.map(esc).join("<br>")}<br><i>¿Lo mantenés en la ruta?</i></div>` : "")
      + `<label class="pop-kg"><span>Kilos a entregar</span><input type="number" min="0" step="any" /></label>`
      + `<label class="chk"><input type="checkbox" /><span>Incluir en ruta</span></label>`;
    const kg = el.querySelector(".pop-kg input"), chk = el.querySelector(".chk input");
    kg.value = c.kg || ""; kg.disabled = !c.incluido; chk.checked = c.incluido;
    kg.addEventListener("input", () => { c.kg = Math.max(0, parseFloat(kg.value) || 0); cambio(); });
    chk.addEventListener("change", () => {
      c.incluido = chk.checked; kg.disabled = !chk.checked;
      if (chk.checked && !c.kg) kg.focus();
      if (c.marker) c.marker.setIcon(icono(c));
      cambio();
    });
    return el;
  }

  const tip = c => `<b>${esc(nombreDe(c))}</b><br><small>${c.incluido ? f1(c.kg) + " kg" : c.facturado ? "Excluido" : "Sin facturación"}</small>`;

  function dibujarClientes() {
    S.capaCli.clearLayers();
    const x = S.ctx; if (!x) return 0;
    L.circleMarker([x.suc.latitude, x.suc.longitude], { radius: 9, color: "#1a1c1e", fillColor: "#fff", fillOpacity: 1, weight: 3 })
      .bindTooltip(esc(x.suc.sucursal), { className: "tip-cliente" }).addTo(S.capaCli);
    const todos = $("chk-todos").checked;
    let visibles = 0;
    x.clientes.forEach(c => {
      c.marker = null;
      if (!(c.facturado || c.incluido || todos)) return;
      visibles++;
      c.marker = L.marker(c.co, { icon: icono(c) })
        .bindPopup(() => popup(c), { minWidth: 240 })
        .bindTooltip(() => tip(c), { className: "tip-cliente" })
        .addTo(S.capaCli);
    });
    return visibles;
  }

  function encuadrar() {
    const x = S.ctx; if (!x) return;
    const pts = [[x.suc.latitude, x.suc.longitude]];
    x.clientes.forEach(c => { if (c.marker) pts.push(c.co); });
    if (pts.length > 1) S.map.fitBounds(pts, { padding: [40, 40] }); else S.map.setView(pts[0], 13);
  }

  function actualizarResumen() {
    const x = S.ctx; if (!x) { $("resumen").innerHTML = ""; return; }
    let n = 0, kg = 0;
    x.clientes.forEach(c => { if (c.incluido) { n++; kg += c.kg || 0; } });
    $("resumen").innerHTML = `<div><b>${n}</b>clientes incluidos</div><div><b>${f1(kg)}</b>kg a entregar</div>`;
  }

  // ── Camiones ──────────────────────────────────────────────────────
  const camionesDe = suc => !S.flota ? [] : S.flota
    .filter(r => mismaSuc(val(r, "sucursal"), suc.sucursal))
    .map(r => { const t = S.tipos.get(norm(val(r, "tipo"))); return t ? RA.armarCam(r, t.row) : null; })
    .filter(c => c && c.cap > 0);

  function resumenCamiones() {
    const boxes = [...$("dd-lista").querySelectorAll("input")];
    $("dd-resumen").textContent = boxes.length ? `${boxes.filter(b => b.checked).length} de ${boxes.length} camiones` : "Sin camiones";
  }

  function llenarCamiones() {
    const lista = $("dd-lista");
    if (!S.flota) { lista.innerHTML = `<div class="dd-vacio">${esc(S.errFlota || "No se cargó la flota.")}</div>`; resumenCamiones(); return; }
    const cams = S.ctx ? camionesDe(S.ctx.suc) : [];
    lista.innerHTML = cams.length
      ? cams.map(c => `<label class="chk"><input type="checkbox" data-p="${esc(c.patente)}"${S.off.has(c.patente) ? "" : " checked"} /><span>${esc(c.patente)} · ${esc(c.tipo)} · ${f1(c.cap)} kg</span></label>`).join("")
      : `<div class="dd-vacio">No hay camiones de esta sucursal en la flota.</div>`;
    resumenCamiones();
  }

  // ── Estado de la planificación ────────────────────────────────────
  function estadoPlan() {
    const e = $("plan-estado");
    $("sec-export").hidden = !S.hoja;
    $("kpis").classList.toggle("stale", S.desact);
    $("btn-csv").disabled = S.desact;
    if (!S.hoja) { e.className = "plan-estado"; e.textContent = ""; return; }
    e.className = "plan-estado " + (S.desact ? "warn" : "ok");
    e.textContent = S.desact ? "Desactualizada: hubo cambios. Generá la hoja de ruta de nuevo antes de exportar." : "Planificación al día.";
  }

  function marcarDesactualizada() {
    if (S.hoja && !S.desact) { S.desact = true; estadoPlan(); }
  }

  function cambio() { actualizarResumen(); marcarDesactualizada(); }

  function limpiarRuta() {
    S.hoja = null; RA.R.hoja = null; S.desact = false; S.avisos = [];
    S.capaRuta.clearLayers();
    $("kpis").hidden = true; $("leyenda").hidden = true;
    estadoPlan();
  }

  function cambiarContexto() {
    if (S.ctx) S.ctx.clientes.forEach(c => { c.avisos = []; c.parada = null; });
    limpiarRuta();
    S.ctx = contexto();
    llenarCamiones();
    const visibles = dibujarClientes();
    actualizarResumen();
    mostrarBanner();
    vacio(!S.ctx ? "Elegí sucursal y fecha." : visibles ? "" : "No hay pedidos para esa sucursal y fecha.");
    encuadrar();
  }

  // ── Resultado: métricas y mapa ────────────────────────────────────
  function pares(h) {
    const rs = h.rutas, L = RA.tot(rs, "litros"), C = RA.tot(rs, "costo");
    const dMax = rs.reduce((m, r) => Math.max(m, r.diasRuta), 0), parcial = k => rs.some(r => r[k] == null) ? " *" : "";
    return [
      ["Camiones", `${rs.length}/${h.flotaTotal}`],
      ["Clientes", rs.reduce((s, r) => s + r.paradas.length, 0)],
      ["Sin asignar", h.sin.length, h.sin.length ? "warn" : ""],
      ["Kg a entregar", f1(RA.tot(rs, "kg") || 0)],
      ["Km " + (h.modo === "osrm" ? "(por calles)" : "(recta)"), f1(RA.tot(rs, "km") || 0)],
      ["Combustible", RA.lts(L) + (L != null ? parcial("litros") : "")],
      ["Costo", RA.money(C) + (C != null ? parcial("costo") : "")],
      ["Días en ruta (máx.)", dMax, dMax > RA.MAX_DIAS_RUTA ? "warn" : ""],
    ];
  }
  const htmlPares = h => pares(h).map(([l, v, cls]) => `<div class="kpi"><span>${l}</span><b class="${cls || ""}">${v}</b></div>`).join("");

  // Oscurece un color "#rrggbb" multiplicando sus canales por f (0-1)
  const oscurecer = (hex, f) => "#" + [1, 3, 5].map(i => Math.round(parseInt(hex.slice(i, i + 2), 16) * f).toString(16).padStart(2, "0")).join("");

  function dibujarRuta() {
    S.capaRuta.clearLayers();
    const h = S.hoja; if (!h) return;
    // Primero todas las idas y después todas las vueltas, así la vuelta (punteada y más oscura)
    // queda siempre por encima, también cuando hay varios camiones.
    h.rutas.forEach((r, i) => {
      const col = COLORES[i % COLORES.length];
      L.polyline(r.geoIda, { color: col, weight: 4, opacity: .75 }).bindPopup(`<b>Ida · ${esc(r.cam.patente)}</b><br>${f1(r.kmCargado)} km (cargado)`).addTo(S.capaRuta);
    });
    h.rutas.forEach((r, i) => {
      const oscuro = oscurecer(COLORES[i % COLORES.length], .7);
      L.polyline(r.geoVuelta, { color: oscuro, weight: 3, opacity: 1, dashArray: "8 8" }).bindPopup(`<b>Vuelta · ${esc(r.cam.patente)}</b><br>${f1(r.kmVacio)} km (vacío)`).addTo(S.capaRuta).bringToFront();
    });
    $("leyenda").innerHTML = `<div><span class="ley-linea"></span>Ida (cargado)</div><div><span class="ley-linea vuelta"></span>Vuelta (vacío)</div>`
      + h.rutas.map((r, i) => `<div><span class="ley-cam" style="background:${COLORES[i % COLORES.length]}"></span>${esc(r.cam.patente)}</div>`).join("");
    $("leyenda").hidden = false;
    $("kpis").innerHTML = htmlPares(h); $("kpis").hidden = false;
  }

  // ── Generar hoja de ruta (misma lógica que planear() del visor) ───
  async function generar() {
    if (S.ocupado) return;
    const x = S.ctx;
    if (!x) return toast("Elegí sucursal y fecha");
    if (!S.flota) return toast("Falta la flota: revisá el detalle en «Datos»");
    const todos = camionesDe(x.suc), camiones = todos.filter(c => !S.off.has(c.patente));
    if (!camiones.length) return toast("Tildá al menos un camión");
    const inc = [...x.clientes.values()].filter(c => c.incluido);
    if (!inc.length) return toast("No hay clientes incluidos en la ruta");
    const sinKg = inc.filter(c => !(c.kg > 0));
    if (sinKg.length) {
      banner([`<b>Faltan los kilos de ${sinKg.length} cliente(s) incluido(s):</b> ${sinKg.map(c => esc(nombreDe(c))).join(", ")}.`]);
      return toast("Ingresá los kilos de los clientes incluidos");
    }

    S.ocupado = true; $("btn-generar").disabled = true; banner(["Consultando OSRM (rutas por calles)…"]);
    try {
      const avisos = [], nom = p => esc(p.nombre || p.id);
      const paradas = inc.map((c, i) => {
        const v = S.ventanas && S.ventanas.get(c.id);
        return { id: c.id, nombre: c.nombre, kg: c.kg, fact: c.fact, co: c.co, vd: v ? v.vd : null, vh: v ? v.vh : null, ix: i + 1 };
      });
      const depot = [x.suc.latitude, x.suc.longitude], pts = [depot, ...paradas.map(p => p.co)];

      let D, modo = "osrm";
      try {
        D = await RA.matriz(pts);
        if (D.nulos) avisos.push(`${D.nulos} tramo(s) sin ruta en OSRM: se usó línea recta para ellos.`);
      } catch (e) {
        modo = "recta"; D = pts.map(a => pts.map(b => RA.dist(a, b))); D.t = D.map(f => f.map(k => k / RA.VEL_RECTA_KMH * 60));
        avisos.push(`<b>OSRM no disponible</b> (${esc(e.message)}): se usó distancia en línea recta.`);
      }

      const T = { M: D.t.map(f => f.map(v => v * RA.FACTOR_MANEJO)), salida: RA.HORA_SALIDA_MIN, serv: RA.servicioMin, maxDias: RA.MAX_DIAS_RUTA, ventanas: !!S.ventanas };
      const plan = RA.planificar(paradas, camiones, D, T), reco = RA.planificar(paradas, todos, D, T), s = RA.resumen(plan), sr = RA.resumen(reco);

      // Trazado de ida (sucursal → última entrega) y de vuelta (última entrega → sucursal)
      for (const r of plan.rutas) {
        const ult = r.paradas[r.paradas.length - 1].co, ida = [depot, ...r.paradas.map(p => p.co)];
        r.geoIda = ida; r.geoVuelta = [ult, depot];
        if (modo === "osrm") {
          try {
            const qa = await RA.osrm("route", { coords: ida }), qv = await RA.osrm("route", { coords: [ult, depot] });
            r.geoIda = qa.routes[0].geometry.coordinates.map(k => [k[1], k[0]]);
            r.geoVuelta = qv.routes[0].geometry.coordinates.map(k => [k[1], k[0]]);
          } catch (e) { avisos.push(`Ruta de ${r.cam.patente}: no se pudo trazar por calles (${esc(e.message)}).`); }
        }
      }

      // Advertencias (textos del visor)
      plan.rutas.filter(r => r.excede).forEach(r => avisos.push(`<b>⚠ ${r.cam.patente} estaría ${r.diasRuta} días en ruta</b> (${RA.fmtMin(r.duracion)} entre manejo y descargas); el máximo es ${RA.MAX_DIAS_RUTA}.`));
      if (s.pat.slice().sort().join() !== sr.pat.slice().sort().join()) avisos.push(`<b>El sistema recomendaría otros camiones.</b> Tu selección: ${RA.txtRes(s)}${plan.sin.length ? ` — ${plan.sin.length} cliente(s) sin asignar` : ""}. Recomendado: ${RA.txtRes(sr)}${reco.sin.length ? ` — ${reco.sin.length} sin asignar` : ""}.`);
      if (plan.sin.length) avisos.push(`<b>${plan.sin.length} cliente(s) sin asignar:</b> ` + plan.sin.map(p => `${nom(p)} (${f1(p.kg)} kg — ${p.motivo})`).join("; "));
      plan.rutas.forEach(r => { if (r.cam.aut > 0 && r.km > r.cam.aut) avisos.push(`${r.cam.patente}: la ruta (${f1(r.km)} km) supera su autonomía estimada.`); });
      if (T.ventanas) {
        plan.rutas.forEach(r => r.paradas.filter(p => p.fuera).forEach(p => avisos.push(`<b>⚠ Fuera de horario:</b> ${nom(p)} (${r.cam.patente}, parada ${r.paradas.indexOf(p) + 1}) llegaría a las ${RA.fmtHoraD(p.llegada)} y su horario es ${RA.txtVentana(p)}.`)));
        avisos.push(`Horarios de entrega cargados para ${paradas.filter(p => p.vd != null || p.vh != null).length} de ${paradas.length} cliente(s) (solo aviso: no cambian el reparto).`);
      }
      const sinC = plan.rutas.filter(r => r.costo == null).map(r => r.cam.patente), sinL = plan.rutas.filter(r => r.litros == null).map(r => r.cam.patente);
      if (sinC.length) avisos.push(`Sin costo_km_cargado / costo_km_vacio para ${sinC.join(", ")}: no se calcula su costo.`);
      if (sinL.length) avisos.push(`Sin datos de consumo para ${sinL.join(", ")}: no se calculan litros (columnas esperadas: consumo en L/100 km o km/L, con "cargado" y "vacio" en el nombre).`);
      avisos.push((modo === "osrm" ? "Km por calles (OSRM, perfil auto: no distingue camiones)" : "Km en línea recta") + `. Todos los clientes salen el mismo día; máx. ${RA.MAX_DIAS_RUTA} días en ruta por camión. Horas estimadas: salida ${RA.fmtHora(RA.HORA_SALIDA_MIN)}, manejo +${Math.round((RA.FACTOR_MANEJO - 1) * 100)} %, descarga 15 min + 0,07 min/kg, sin descansos. Km cargado = sucursal → última entrega; km vacío = vuelta.`);

      // Clientes demasiado lejos respecto del resto de su ruta (distancia a la sucursal)
      const lejos = new Map();
      plan.rutas.forEach(r => {
        if (r.paradas.length < LEJOS_MIN_PARADAS) return;
        r.paradas.forEach(p => {
          const d = D[0][p.ix], otros = r.paradas.filter(q => q !== p), prom = otros.reduce((a, q) => a + D[0][q.ix], 0) / otros.length;
          if (d > LEJOS_FACTOR * prom && d - prom >= LEJOS_MIN_EXTRA_KM) {
            lejos.set(p.id, { d, prom });
            avisos.push(`<b>⚠ ${nom(p)} (${r.cam.patente}) está muy lejos del resto:</b> a ${f1(d)} km de la sucursal, ${f1(d / prom)} veces el promedio de los demás clientes de la ruta (${f1(prom)} km).`);
          }
        });
      });

      // Advertencias por cliente (para el color rojo y el globo del mapa)
      x.clientes.forEach(c => { c.avisos = []; c.parada = null; });
      plan.rutas.forEach(r => r.paradas.forEach((p, j) => {
        const c = x.clientes.get(p.id); c.parada = { pat: r.cam.patente, orden: j + 1 };
        if (p.fuera) c.avisos.push(`Fuera de horario: llegaría a las ${RA.fmtHoraD(p.llegada)} y su horario es ${RA.txtVentana(p)}.`);
        // Autonomía y días en ruta: solo mensaje en el banner (no pintan los clientes de rojo).
        const l = lejos.get(p.id);
        if (l) c.avisos.push(`Está a ${f1(l.d)} km de la sucursal: ${f1(l.d / l.prom)} veces más lejos que el promedio del resto de la ruta (${f1(l.prom)} km).`);
      }));
      plan.sin.forEach(p => { const c = x.clientes.get(p.id); if (c) c.avisos.push(`Sin asignar: ${p.motivo}.`); });

      S.hoja = { suc: x.suc, fecha: x.fecha, depot, modo, ...plan, flotaTotal: camiones.length, ventanas: T.ventanas };
      RA.R.hoja = S.hoja; S.avisos = avisos; S.desact = false;
      dibujarClientes(); dibujarRuta(); estadoPlan(); mostrarBanner();
    } catch (e) {
      banner([`<b>No se pudo generar la hoja de ruta:</b> ${esc(e.message || e)}`]);
      toast("Error al generar la hoja de ruta");
    } finally { S.ocupado = false; $("btn-generar").disabled = false; }
  }

  // ── Exportación con confirmación ──────────────────────────────────
  const cerrarModal = () => { $("modal").hidden = true; };
  function abrirResumen() {
    if (!S.hoja || S.desact) return toast("Generá la hoja de ruta de nuevo antes de exportar");
    $("modal-resumen").innerHTML = htmlPares(S.hoja);
    $("modal-avisos").innerHTML = S.avisos.map(a => `<span>${a}</span>`).join("");
    $("modal").hidden = false; $("modal-confirmar").focus();
  }

  // ── Inicio ────────────────────────────────────────────────────────
  initMap();
  $("sel-sucursal").addEventListener("change", cambiarContexto);
  $("sel-fecha").addEventListener("change", cambiarContexto);
  $("chk-todos").addEventListener("change", () => { dibujarClientes(); });
  $("dd-lista").addEventListener("change", e => {
    const p = e.target.dataset.p; if (!p) return;
    e.target.checked ? S.off.delete(p) : S.off.add(p);
    resumenCamiones(); marcarDesactualizada();
  });
  $("btn-generar").addEventListener("click", generar);
  $("btn-csv").addEventListener("click", abrirResumen);
  $("modal-cancelar").addEventListener("click", cerrarModal);
  $("modal-confirmar").addEventListener("click", () => { RA.descargar(); cerrarModal(); toast("Hoja de ruta descargada"); });
  $("modal").addEventListener("click", e => { if (e.target === $("modal")) cerrarModal(); });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("modal").hidden) cerrarModal(); });

  cargarDatos().then(ok => {
    if (!ok) { vacio("No se pudieron cargar los datos. Revisá el detalle en «Datos»."); toast("Error al cargar datos"); return; }
    $("sel-sucursal").innerHTML = S.suc.map((s, i) => `<option value="${i}">${esc(s.sucursal)}</option>`).join("");
    const fechas = fechasDisponibles(S.seg), fs = $("sel-fecha");
    fs.innerHTML = fechas.length ? fechas.map(f => `<option value="${f.key}">${f.label}</option>`).join("") : `<option value="">Sin fechas</option>`;
    fs.disabled = !fechas.length;
    cambiarContexto();
  }).catch(e => { vacio("No se pudieron cargar los datos."); toast(e.message); });
})();