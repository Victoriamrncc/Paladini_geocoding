"use strict";
/*
 * ruta_algoritmo.js — COPIA literal (generada por script) del bloque de
 * "Hoja de ruta" de visor.html: parámetros, algoritmo (armar / planificar),
 * matriz y rutas OSRM, consumo y costos de flota, totales y CSV.
 * No contiene lógica nueva. Si cambia el algoritmo del visor, hay que volver
 * a copiarlo. Requiere ruta_logica.js (logParseNumber, etc.) cargado antes.
 */
const RA = (function () {
  const R = { hoja: null };   // descargar() lee la hoja de R.hoja
      const norm = t => String(t == null ? "" : t).normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim().toLowerCase();
      const ksuc = t => norm(t).replace("paladini s.a.", " ").replace("sucursal", " ").replace(/-/g, " ").replace(/\s+/g, " ").trim();
      const mismaSuc = (a, b) => { const x = ksuc(a), y = ksuc(b); return !!x && !!y && (x === y || x.includes(y) || y.includes(x)); };
      const val = (row, k) => { const f = Object.keys(row).find(h => norm(h) === k); return f ? row[f] : ""; };
      const num = v => v === "" || v == null ? null : logParseNumber(v);
      const f1 = n => (Math.round(n * 10) / 10).toLocaleString("es-AR");
      const pad = n => String(n).padStart(2, "0");
      const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
      const money = n => n == null ? "—" : "$ " + Math.round(n).toLocaleString("es-AR");
      const lts = n => n == null ? "—" : f1(n) + " L";

      // ── Parámetros (ajustables) ──
      const MAX_DIAS_RUTA = 2;            // un camión puede estar hasta 2 días en ruta (se avisa si se pasa)
      const HORA_SALIDA_MIN = 8 * 60;     // hora de salida supuesta (solo para estimar horas de llegada)
      const FACTOR_MANEJO = 1.15;         // OSRM usa perfil auto: se suma 15 % al tiempo de manejo
      const VEL_RECTA_KMH = 40;           // velocidad supuesta si no hay tiempos de OSRM
      const servicioMin = kg => Math.min(90, 15 + 0.07 * kg);   // descarga: 15 min + 0,07 min por kg (máx. 90)
      const fmtMin = m => `${Math.floor(m / 60)} h ${pad(Math.round(m % 60))} min`;
      const fmtHora = m => `${pad(Math.floor(m / 60) % 24)}:${pad(Math.round(m % 60) % 60)}`;
      const fmtHoraD = m => fmtHora(m) + (m >= 1440 ? ` (+${Math.floor(m / 1440)} d)` : "");
      const aMin = t => { const m = String(t == null ? "" : t).trim().match(/^(\d{1,2})[:.hH](\d{2})/); return m ? (+m[1]) * 60 + (+m[2]) : null; };
      const txtVentana = p => p.vd != null && p.vh != null ? `${fmtHora(p.vd)} – ${fmtHora(p.vh)}` : p.vd != null ? `desde ${fmtHora(p.vd)}` : p.vh != null ? `hasta ${fmtHora(p.vh)}` : "";

      /*ALGO*/
      const dist = (a, b) => {
        const r = Math.PI / 180, dl = (b[0] - a[0]) * r, dn = (b[1] - a[1]) * r;
        const h = Math.sin(dl / 2) ** 2 + Math.cos(a[0] * r) * Math.cos(b[0] * r) * Math.sin(dn / 2) ** 2;
        return 12742.0176 * Math.asin(Math.sqrt(h));
      };
      const sumKg = g => g.reduce((s, p) => s + p.kg, 0);
      const mul = (a, b) => a == null || b == null ? null : a * b;
      const suma = (a, b) => a == null || b == null ? null : a + b;
      const por100 = v => v == null ? null : v / 100;
      // Arma la ruta de un camión: orden por vecino más cercano desde la sucursal, horas de llegada,
      // litros y costo (cargado = sucursal → última entrega; vacío = vuelta). T = { M, salida, serv, maxDias, ventanas }.
      // Los horarios de clientes (p.vd / p.vh, minutos del día) solo generan aviso (p.fuera), no cambian el orden.
      function armar(cam, g, D, T) {
        let resto = g.slice(), act = 0, acum = 0, t = T ? T.salida : 0; const ps = [];
        while (resto.length) {
          const sig = resto.reduce((m, p) => D[act][p.ix] < D[act][m.ix] ? p : m); resto = resto.filter(p => p !== sig);
          const tramo = D[act][sig.ix]; acum += tramo;
          const ll = T ? t + T.M[act][sig.ix] : 0, tod = ll % 1440;
          const fuera = !!(T && T.ventanas && ((sig.vd != null && tod < sig.vd) || (sig.vh != null && tod > sig.vh)));
          ps.push({ ...sig, tramo, acum, llegada: ll, fuera, litros: mul(tramo, por100(cam.l100c)), costo: mul(tramo, cam.ckc) });
          if (T) t = ll + T.serv(sig.kg);
          act = sig.ix;
        }
        const vuelta = D[act][0], fin = T ? t + T.M[act][0] : 0, dur = T ? fin - T.salida : 0;
        const lc = mul(acum, por100(cam.l100c)), lv = mul(vuelta, por100(cam.l100v)), cc = mul(acum, cam.ckc), cv = mul(vuelta, cam.ckv);
        return {
          cam, paradas: ps, kg: sumKg(g), vuelta, kmCargado: acum, kmVacio: vuelta, km: acum + vuelta, salida: T ? T.salida : 0, fin, duracion: dur,
          diasRuta: Math.max(1, Math.ceil(dur / 1440)), excede: !!T && dur > T.maxDias * 1440, litrosC: lc, litrosV: lv, litros: suma(lc, lv), costoC: cc, costoV: cv, costo: suma(cc, cv)
        };
      }
      // D = matriz de km (índice 0 = sucursal; cada parada trae su índice "ix"). Todos los clientes salen el mismo día:
      // se llena primero el camión más grande (cliente más lejano + los más cercanos entre sí hasta completar la
      // capacidad), después el siguiente, y así. Lo que no entra en ningún camión queda sin asignar.
      function planificar(paradas, camiones, D, T) {
        const maxCap = Math.max(...camiones.map(c => c.cap));
        let pend = paradas.slice(); const rutas = [];
        for (const cam of camiones.slice().sort((a, b) => b.cap - a.cap)) {
          if (!pend.length) break;
          let libre = cam.cap, c = pend.filter(p => p.kg <= libre);
          if (!c.length) continue;
          const sem = c.reduce((m, p) => D[0][p.ix] > D[0][m.ix] ? p : m);
          const g = [sem]; pend = pend.filter(p => p !== sem); libre -= sem.kg;
          for (; ;) {
            c = pend.filter(p => p.kg <= libre); if (!c.length) break;
            const prom = x => g.reduce((s, q) => s + D[q.ix][x.ix], 0) / g.length;
            const sig = c.reduce((m, p) => prom(p) < prom(m) ? p : m);
            g.push(sig); pend = pend.filter(p => p !== sig); libre -= sig.kg;
          }
          rutas.push(armar(cam, g, D, T));
        }
        const sin = pend.map(p => ({ ...p, motivo: p.kg > maxCap ? "Pedido más pesado que cualquier camión" : "Sin capacidad en los camiones elegidos" }));
        return { rutas, sin };
      }
      /*FIN*/      async function osrm(servicio, body) {
        const r = await fetch("/api/osrm", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ servicio, ...body }) });
        let j = {}; try { j = await r.json(); } catch (e) { }
        if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
        return j;
      }
      async function matriz(co) {
        const n = co.length, B = 45, D = Array.from({ length: n }, () => Array(n).fill(0)), Tm = Array.from({ length: n }, () => Array(n).fill(0)); let nulos = 0;
        for (let i = 0; i < n; i += B) for (let j = 0; j < n; j += B) {
          const fi = [], fj = [];
          for (let k = i; k < Math.min(i + B, n); k++) fi.push(k);
          for (let k = j; k < Math.min(j + B, n); k++) fj.push(k);
          const idx = [...new Set([...fi, ...fj])], pos = new Map(idx.map((k, p) => [k, p]));
          const res = await osrm("table", { coords: idx.map(k => co[k]), sources: fi.map(k => pos.get(k)), destinations: fj.map(k => pos.get(k)) });
          fi.forEach((a, r) => fj.forEach((b, c) => {
            const v = res.distances[r][c];
            if (v == null) { nulos++; D[a][b] = dist(co[a], co[b]); } else D[a][b] = v / 1000;
            const dv = res.durations && res.durations[r] ? res.durations[r][c] : null;
            Tm[a][b] = dv == null ? D[a][b] / VEL_RECTA_KMH * 60 : dv / 60;
          }));
        }
        if (nulos) D.nulos = nulos;
        D.t = Tm;
        return D;
      }
      // ── Flota, consumos y costos ──
      // Consumo: se busca una columna de consumo/rendimiento con "cargado" o "vacio" en el nombre (L/100 km o km/L;
      // si no dice la unidad, un valor >= 10 se toma como L/100 km). Costos: costo_km_cargado y costo_km_vacio.
      // Las columnas se leen de flota.csv (por patente) y, si no están ahí, de tipos_camion.csv (por tipo).
      function consumo(row, modo) {
        const clave = modo === true ? "cargado" : modo === false ? "vacio" : null;
        for (const h of Object.keys(row)) {
          const n = norm(h).replace(/[^a-z0-9]+/g, "_"), v = logParseNumber(row[h]);
          if (row[h] === "" || !(v > 0) || n.includes("costo") || !/(consumo|litro|rendim|l_100|km_l|kml|km_por)/.test(n)) continue;
          if (clave ? !n.includes(clave) : (n.includes("cargado") || n.includes("vacio"))) continue;
          const kml = /km_l|kml|km_por|rendim/.test(n) ? true : /100/.test(n) ? false : v < 10;
          return { l100: kml ? 100 / v : v, col: h };
        }
        return null;
      }
      function armarCam(row, tRow) {
        const m = { ...tRow }; Object.entries(row).forEach(([k, v]) => { if (v !== "") m[k] = v; });
        const cc = consumo(m, true) || consumo(m, false) || consumo(m, null), cv = consumo(m, false) || cc;
        return {
          patente: val(m, "patente"), tipo: String(val(m, "tipo")).toUpperCase(), cap: logParseNumber(val(m, "capacidad_planificacion_kg")), aut: logParseNumber(val(m, "autonomia_km")),
          ckc: num(val(m, "costo_km_cargado")), ckv: num(val(m, "costo_km_vacio")), l100c: cc ? cc.l100 : null, l100v: cv ? cv.l100 : null
        };
      }
      const tot = (rs, k) => { const x = rs.map(r => r[k]).filter(v => v != null); return x.length ? x.reduce((a, b) => a + b, 0) : null; };
      const resumen = p => ({ n: p.rutas.length, pat: p.rutas.map(r => r.cam.patente), km: tot(p.rutas, "km") || 0, litros: tot(p.rutas, "litros"), costo: tot(p.rutas, "costo") });
      const txtRes = s => `${s.n} camión(es) (${s.pat.join(", ")}) · ${f1(s.km)} km · ${lts(s.litros)} · ${money(s.costo)}`;

      const resRuta = r => `${f1(r.km)} km (${f1(r.kmCargado)} cargado + ${f1(r.kmVacio)} vacío) · ${lts(r.litros)} · ${money(r.costo)}`;
      // ── Salidas (CSV) ──
      const n2 = x => x == null ? "" : String(Math.round(x * 100) / 100).replace(".", ",");
      function bajarCsv(nombre, f) {
        const csv = "\uFEFF" + f.map(l => l.map(c => `"${String(c).replace(/"/g, '""')}"`).join(";")).join("\r\n");
        const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
        a.download = nombre; a.click();
      }
      const base = h => `${ksuc(h.suc.sucursal).replace(/ /g, "_")}_${h.fecha}`;
      function descargar() {
        const h = R.hoja; if (!h) return;
        const f = [["fecha", "sucursal", "patente", "orden", "id_cliente", "cliente", "facturas", "kg", "km_tramo", "km_acumulado", "litros_tramo", "litros_acumulado", "hora_llegada_estimada", "dia_llegada", "ventana_desde", "ventana_hasta", "alerta"]];
        h.rutas.forEach(r => {
          let la = r.litros == null ? null : 0;
          r.paradas.forEach((p, j) => {
            if (la != null && p.litros != null) la += p.litros; else la = null;
            f.push([h.fecha, h.suc.sucursal, r.cam.patente, j + 1, p.id, p.nombre, p.fact.join(" "), n2(p.kg), n2(p.tramo), n2(p.acum), n2(p.litros), n2(la), fmtHora(p.llegada), Math.floor(p.llegada / 1440) + 1,
            h.ventanas && p.vd != null ? fmtHora(p.vd) : "", h.ventanas && p.vh != null ? fmtHora(p.vh) : "", p.fuera ? "FUERA DE HORARIO" : ""]);
          });
          f.push([h.fecha, h.suc.sucursal, r.cam.patente, "VUELTA", "", "SUCURSAL", "", "", n2(r.vuelta), n2(r.km), n2(r.litrosV), n2(r.litros), fmtHora(r.fin), Math.floor(r.fin / 1440) + 1, "", "", r.excede ? `${r.diasRuta} DIAS EN RUTA` : ""]);
        });
        bajarCsv(`hoja_ruta_${base(h)}.csv`, f);
      }

  return {
    R, dist, planificar, armar, matriz, armarCam, consumo, osrm, tot, resumen, txtRes, resRuta, descargar,
    num, money, lts, fmtMin, fmtHora, fmtHoraD, aMin, txtVentana, servicioMin,
    MAX_DIAS_RUTA, HORA_SALIDA_MIN, FACTOR_MANEJO, VEL_RECTA_KMH
  };
})();
