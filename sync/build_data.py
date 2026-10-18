#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arma data.json para la página "Propuesta de Valor" (CGO Distancia) a partir de:

  sync/inputs/feedback.json   ← '🔰FEEDBACK'!A1:R2000   (archivo I, spreadsheetId 1SJAgbcxpVXpRZ6_Vb4_VW1Pl9BJZMyR8cTH-hmvgdEE)
  sync/inputs/tecnicas.json   ← '🏆 TECNICAS'!A1:Z60    (archivo I)
  sync/inputs/gen.json        ← '🔰GEN'!A1:J56          (archivo I, tablero: lista de asesores del ranking; su mes en B6)
  sync/inputs/ingmat.json     ← '🧮ING/MAT'!A1:AW3000   (archivo I, ingresantes/matriculados: de aquí salen las matrículas del MES EN CURSO por asesor;
                                                          respaldo: una columna por archivo ingmat_E/O/AE/AP/AU.json)
  sync/inputs/metas.json      ← '🧮GC'!A1:J400          (archivo I: tabla METAS POR MES; se ubica sola por su encabezado)
  sync/inputs/diasem.json     ← '🔰DIA/SEM'!A19:IF23    (archivo I; solo respaldo si falta gen.json)
  sync/inputs/dashboard.json  ← 'DASHBOARD'!B5:O90      (Matriz_CGO_Distancia_2027-I, spreadsheetId 1MaiDiBNBicpttRJs2oSAyKvbAD0nI963anN9wfYoeOM:
                                                          cuartil de calidad, mapa de calor y focos del equipo)
  sync/inputs/checklist.json  ← '✅ CHECKLIST'!A1:AZ80  (archivo I; OPCIONAL: validación manual del supervisor)
  sync/casos_cache.json       ← speeches escritos por Claude para el caso de cada asesor (se reutilizan mientras no cambie la evaluación)
  sync/checklist_cache.json   ← lectura de Claude del checklist de calidad por LLAMADA evaluada (clave = hash de la fila)
  index.html                  ← biblioteca de técnicas (db.tecnicas) y checklist de cada paso (db.atencion.pasos)

Cada archivo de inputs es el JSON tal cual lo devuelve el conector de Google Sheets ({"range": "...", "values": [[...], ...]}).
Todo lo determinista vive aquí; lo único que requiere criterio (escribir un speech, leer si un ítem del checklist
se cumplió en una llamada) queda listado en sync/pending.json para que la tarea programada lo complete y vuelva a correr este script.

Uso:  python3 sync/build_data.py          (desde la raíz del repo; escribe data.json y sync/pending.json)
      python3 sync/build_data.py --dry    (solo reporta, no escribe data.json)
"""
import json, re, sys, os, hashlib, unicodedata, datetime, calendar, copy
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC = os.path.join(ROOT, 'sync')
INP = os.path.join(SYNC, 'inputs')
DRY = '--dry' in sys.argv

# ----------------------------------------------------------------------------- parámetros del modelo
# Avance de matrículas que define el cuartil (y el cuadrante):
#   'tablero' → como el tablero 🔰GEN de Christopher: % = matrículas del mes / meta del mes del asesor, y el cuartil es
#               RELATIVO al equipo (QUARTILE de las matrículas del mes: Q1 = el 25% que más lleva), justo a cualquier día del mes
#   'ritmo'   → (respaldo con 🔰DIA/SEM) matrículas / meta prorrateada a los días hábiles transcurridos
#   'mes'     → (respaldo con 🔰DIA/SEM) matrículas / meta completa del mes, cuartil por umbral fijo
AVANCE_MODO = 'tablero'
AVANCE_TOPE_INDICE = 150        # en el índice del ranking el avance cuenta hasta este %, para que un pico temprano no distorsione
VENTANA_CHECKLIST = 3           # un ítem validado se conserva mientras aparezca en alguna de las últimas N llamadas evaluadas
                                # y ninguna posterior lo marque como pendiente
LIMA = datetime.timezone(datetime.timedelta(hours=-5))

# Alias permanentes: el sistema de origen escribe algunos nombres distinto en cada pestaña y no se puede corregir ahí.
# Clave = como aparece en alguna fuente; valor = nombre canónico (el de 🔰FEEDBACK).
ALIASES = {
    "Edward Nelson Gonzales Vilaneda": "Edward Nelson Gonzales Villaneda",
}
STAGE_KEYS = ['paso1', 'paso2', 'paso3', 'paso4']
STAGE_LABEL = {'paso1': 'ganar confianza', 'paso2': 'identificar necesidades', 'paso3': 'solucionar problemas', 'paso4': 'cerrar'}
STAGE_LABEL_ALT = {'paso4': ['cierre', 'cerrar']}
CUADRANTE_RANK = {'verde': 0, 'amarillo': 1, 'naranja': 2, 'rojo': 3}


# ----------------------------------------------------------------------------- utilidades
def norm(s):
    s = unicodedata.normalize('NFD', str(s if s is not None else '').lower())
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]', '', s)

def pct(v):
    """'72%' → 72 · '0,72' → 72 · '136,36%' → 136.36 · '' → None"""
    if v is None:
        return None
    s = str(v).replace('%', '').replace(',', '.').strip()
    if s == '':
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    if f <= 1.0 and '%' not in str(v):
        f = f * 100
    return round(f, 2)

def ipct(v):
    p = pct(v)
    return None if p is None else int(round(p))

def to_int(v):
    s = str(v if v is not None else '').replace(',', '.').strip()
    if s in ('', '-', '—'):
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None

def parse_date(s):
    m = re.match(r'\s*(\d{1,2})/(\d{1,2})/(\d{4})', str(s or ''))
    if not m:
        return None
    d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime.date(y, mo, d)
    except ValueError:
        return None

def sha12(s):
    return hashlib.sha1(s.encode('utf-8')).hexdigest()[:12]

def load_values(name, required=True):
    path = os.path.join(INP, name)
    if not os.path.exists(path):
        if required:
            sys.exit(f'FALTA sync/inputs/{name}')
        return None
    with open(path, encoding='utf-8') as f:
        j = json.load(f)
    vals = j.get('values', j) if isinstance(j, dict) else j
    if not isinstance(vals, list):
        sys.exit(f'sync/inputs/{name}: formato inesperado')
    return vals

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def canon_name(raw):
    """Nombre canónico: alias explícito → coincidencia exacta normalizada → parecido fuerte (misma persona escrita distinto)."""
    n = str(raw or '').strip()
    n = re.sub(r'\s+', ' ', n)
    if not n:
        return ''
    if n in ALIASES:
        return ALIASES[n]
    key = norm(n)
    if key in CANON_BY_NORM:
        return CANON_BY_NORM[key]
    best, second, best_name = 0.0, 0.0, None
    for ck, cname in CANON_BY_NORM.items():
        r = SequenceMatcher(None, key, ck).ratio()
        if r > best:
            second, best, best_name = best, r, cname
        elif r > second:
            second = r
    if best >= 0.80 and best - second >= 0.05:
        UNMATCHED_LOG.append(f'  ~ "{n}" → "{best_name}" ({best:.2f})')
        return best_name
    UNMATCHED_LOG.append(f'  ✗ "{n}" sin equivalente en 🔰FEEDBACK (mejor parecido {best:.2f})')
    return n

def stage_from_label(label):
    nl = norm(label)
    if not nl:
        return None
    for k in STAGE_KEYS:
        cands = [STAGE_LABEL[k]] + STAGE_LABEL_ALT.get(k, [])
        if any(norm(c) in nl for c in cands):
            return k
    return None

def stage_col(headers, k):
    """Columna del % de la etapa (la que empieza con el nombre de la etapa y no es de texto)."""
    cands = [STAGE_LABEL[k]] + STAGE_LABEL_ALT.get(k, [])
    for h in headers:
        nh = norm(h)
        if any(nh.startswith(norm(c)) for c in cands) and 'fortaleza' not in nh and 'oportunidad' not in nh:
            return h
    return None

def stage_text_col(headers, k, kind):
    """Columna de texto por etapa del formato nuevo: "Ganar confianza (15%) - Fortaleza" / "... - Oportunidades"."""
    cands = [STAGE_LABEL[k]] + STAGE_LABEL_ALT.get(k, [])
    for h in headers:
        nh = norm(h)
        if any(nh.startswith(norm(c)) for c in cands) and kind in nh:
            return h
    return None

def col_exacta(headers, *names):
    ns = [norm(x) for x in names]
    for h in headers:
        if norm(h) in ns:
            return h
    return None

def col_fecha(headers):
    """Fecha de la evaluación: "Fecha", "FECHA DE EVA." o similar; nunca "Fecha ingreso"."""
    for h in headers:
        if norm(h) == 'fecha':
            return h
    for h in headers:
        nh = norm(h)
        if 'fecha' in nh and 'ingreso' not in nh and ('eva' in nh or 'llamada' in nh):
            return h
    for h in headers:
        nh = norm(h)
        if 'fecha' in nh and 'ingreso' not in nh:
            return h
    return None

def col(headers, *names):
    """Encabezado cuya forma normalizada sea exactamente uno de los nombres (prioridad) o lo contenga."""
    ns = [norm(x) for x in names]
    for h in headers:
        if norm(h) in ns:
            return h
    for h in headers:
        if any(n in norm(h) for n in ns):
            return h
    return None

def dias_habiles(year, month, hasta=None):
    """Días del mes sin contar domingos (así cuenta 🔰DIA/SEM los días restantes). hasta = último día incluido."""
    n = calendar.monthrange(year, month)[1]
    tope = hasta or n
    total = sum(1 for d in range(1, n + 1) if datetime.date(year, month, d).weekday() != 6)
    transc = sum(1 for d in range(1, tope + 1) if datetime.date(year, month, d).weekday() != 6)
    return transc, total


# ----------------------------------------------------------------------------- index.html → db
def read_db_from_index():
    path = os.path.join(ROOT, 'index.html')
    with open(path, encoding='utf-8') as f:
        html = f.read()
    start = html.index('let db = ') + len('let db = ')
    end = html.index(';\n\n  let activeFilterGlobal')
    return json.loads(html[start:end])

db = read_db_from_index()
TECNICAS_LIB = db.get('tecnicas', [])
TEC_BY_ID = {t['id']: t for t in TECNICAS_LIB}
PASOS = db.get('atencion', {}).get('pasos', [])
CHECK_ITEMS = []   # [{id, paso, num, texto}]
for p in PASOS:
    for i, txt in enumerate(p.get('checklist', []), 1):
        CHECK_ITEMS.append({'id': f'{p["id"]}_{i}', 'paso': p['id'], 'num': i, 'texto': txt})
CHECK_HASH = sha12('|'.join(norm(c['texto']) for c in CHECK_ITEMS))


# ----------------------------------------------------------------------------- 🔰FEEDBACK → evaluacion
fb = load_values('feedback.json')
headers = [str(h) for h in fb[0]]
H_ASESOR = col(headers, 'asesor')
H_FECHA = col_fecha(headers)
H_SCORE = col(headers, 'score cualitativo', 'score')
H_CUARTIL = col(headers, 'cuartil')
H_SEDE = col(headers, 'sede')
H_ANTIG = col(headers, 'tramo antiguedad', 'antiguedad')
H_FORT_P = col(headers, 'fortaleza principal')
H_BRECHA_P = col(headers, 'brecha principal')
H_FORTS = col_exacta(headers, 'fortalezas')            # formato anterior: un solo texto general
H_OPORT = col_exacta(headers, 'oportunidades')
H_EVID = col(headers, 'evidencia / observacion', 'evidencia')
H_STAGE = {k: stage_col(headers, k) for k in STAGE_KEYS}
H_FORT_E = {k: stage_text_col(headers, k, 'fortaleza') for k in STAGE_KEYS}      # formato nuevo: texto por etapa
H_OPOR_E = {k: stage_text_col(headers, k, 'oportunidad') for k in STAGE_KEYS}
FORMATO_ETAPAS = all(H_FORT_E.values()) and all(H_OPOR_E.values())
# Desde esta fecha las columnas por etapa se leen tal cual. Antes, un texto que solo esté en las
# columnas de "Cerrar" es el texto general heredado (FORTALEZAS / OPORTUNIDADES del formato anterior).
FECHA_FORMATO_ETAPAS = datetime.date(2026, 10, 13)
missing = [k for k, v in H_STAGE.items() if not v]
if not H_ASESOR or missing or not H_FECHA:
    sys.exit(f'🔰FEEDBACK: no encuentro columnas clave (asesor={H_ASESOR}, fecha={H_FECHA}, etapas faltantes={missing})')

evaluacion = []
for raw in fb[1:]:
    if not any(str(c).strip() for c in raw):
        continue
    row = {}
    for i, h in enumerate(headers):
        v = raw[i] if i < len(raw) else ''
        row[h] = v if v is not None else ''
    name = str(row.get(H_ASESOR, '')).strip()
    name = re.sub(r'\s+', ' ', name)
    if not name:
        continue
    row[H_ASESOR] = ALIASES.get(name, name)
    if H_FECHA != 'Fecha':
        row['Fecha'] = str(row.get(H_FECHA, '') or '').strip()   # la página lee "Fecha"
    evaluacion.append(row)

# Respaldo del formato anterior (FORTALEZAS / OPORTUNIDADES generales + fortaleza y brecha principal
# etiquetadas por el supervisor) para las evaluaciones hechas antes del formato por etapa.
LEGACY = {}
_lv = load_json(os.path.join(SYNC, 'legacy_feedback.json'), None)
if isinstance(_lv, dict) and _lv.get('values'):
    lh = [str(h) for h in _lv['values'][0]]
    li = {n: (lh.index(c) if c in lh else None) for n, c in (('asesor', col(lh, 'asesor')), ('fecha', col_fecha(lh)), ('score', col(lh, 'score cualitativo', 'score')),
                                                            ('fp', col(lh, 'fortaleza principal')), ('bp', col(lh, 'brecha principal')), ('forts', col_exacta(lh, 'fortalezas')), ('oport', col_exacta(lh, 'oportunidades')))}
    for raw in _lv['values'][1:]:
        g = lambda key: (str(raw[li[key]]).strip() if li.get(key) is not None and li[key] < len(raw) and raw[li[key]] is not None else '')
        nm = re.sub(r'\s+', ' ', g('asesor'))
        if not nm:
            continue
        nm = ALIASES.get(nm, nm)
        LEGACY.setdefault((norm(nm), norm(g('fecha'))), []).append({'score': norm(g('score')), 'fortalezaPrincipal': g('fp'), 'brechaPrincipal': g('bp'), 'fortalezas': g('forts'), 'oportunidades': g('oport')})

def legacy_de(row):
    lst = LEGACY.get((norm(row.get(H_ASESOR)), norm(row.get(H_FECHA))))
    if not lst:
        return None
    sc = norm(row.get(H_SCORE))
    for e in lst:
        if e['score'] == sc:
            return e
    return lst[0] if len(lst) == 1 else None

CANON_BY_NORM = {}
for row in evaluacion:
    CANON_BY_NORM.setdefault(norm(row[H_ASESOR]), row[H_ASESOR])
UNMATCHED_LOG = []


# ----------------------------------------------------------------------------- 🏆 TECNICAS → tecnicas
tecnicas = []
tv = load_values('tecnicas.json', required=False) or []
if len(tv) >= 2:
    th = [str(x) for x in tv[1]]
    for raw in tv[2:]:
        if not raw or not str(raw[0]).strip():
            continue
        asesor = canon_name(raw[0])
        sede = str(raw[1]).strip() if len(raw) > 1 else ''
        for ci in range(2, len(th)):
            v = raw[ci] if ci < len(raw) else ''
            if v is True or str(v).strip().lower() == 'true':
                tecnicas.append({'Asesor': asesor, 'Sede': sede, 'Técnica': th[ci].strip()})
tec_count = {}
for t in tecnicas:
    tec_count[norm(t['Asesor'])] = tec_count.get(norm(t['Asesor']), 0) + 1


# ----------------------------------------------------------------------------- 🔰DIA/SEM → avance del mes por asesor
hoy = datetime.datetime.now(LIMA).date()
DH_TRANSC, DH_TOTAL = dias_habiles(hoy.year, hoy.month, hoy.day)
FRACCION_MES = DH_TRANSC / DH_TOTAL if DH_TOTAL else 1.0
mes_by_name = {}
dv = load_values('diasem.json', required=False)
if dv and len(dv) >= 5:
    r_nom, r_hdr, r_val, r_lbl, r_logro = [list(map(str, r)) for r in dv[:5]]
    g = lambda r, i: (r[i] if i < len(r) else '')
    meta_global_mat = to_int(g(r_logro, 3))   # columnas A-D fijas: Meta PRE/POS/ING/MAT
    c = 0
    while c < max(len(r_nom), len(r_hdr)):
        if norm(g(r_hdr, c)) == 'preinscritos' and str(g(r_nom, c)).strip():
            name = canon_name(g(r_nom, c))
            pre, pos, ing, mat = (to_int(g(r_val, c + i)) or 0 for i in range(4))
            logro_mat = pct(g(r_logro, c + 3))
            meta = int(round(mat / (logro_mat / 100.0))) if (mat > 0 and logro_mat) else meta_global_mat
            mes_by_name[norm(name)] = {'pre': pre, 'pos': pos, 'ing': ing, 'mat': mat, 'meta': meta,
                                       'logroMes': round(100.0 * mat / meta, 2) if meta else None}
            c += 7
        else:
            c += 1

def cuartil_matriculas(av):
    if av is None: return None
    return 'Q1' if av >= 100 else 'Q2' if av >= 80 else 'Q3' if av >= 70 else 'Q4'

def quartile_inc(vals, q):
    """QUARTILE.INC de Sheets/Excel (interpolación lineal) sobre una lista de números."""
    v = sorted(float(x) for x in vals)
    if not v:
        return None
    pos = (len(v) - 1) * q / 4.0
    lo = int(pos); hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)

# ----------------------------------------------------------------------------- 🧮GC → meta mensual por asesor
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
meta_by_name = {}
mv = load_values('metas.json', required=False)
# La tabla "METAS POR MES" de 🧮GC se mueve hacia abajo cuando crecen las tablas de arriba, así que se
# lee un bloque amplio ('🧮GC'!A1:J400) y se ubica por su encabezado: la fila con "Preinscripción" y
# los meses. La columna de nombres es la de "Preinscripción"; la tabla termina en DIV-/SUPERVISOR/Suma.
if mv:
    hr = next((i for i, r in enumerate(mv) if r and any(norm(x) == norm('Preinscripción') for x in r)
               and any(norm(x) == norm(MESES[hoy.month - 1]) for x in r)), None)
    if hr is None:
        print('⚠️ 🧮GC: no encuentro la tabla "METAS POR MES" (fila con "Preinscripción" y los meses): avance sin meta')
    else:
        mh = [norm(x) for x in mv[hr]]
        cn = mh.index(norm('Preinscripción'))
        ci = mh.index(norm(MESES[hoy.month - 1]))
        for raw in mv[hr + 1:]:
            nombre = str(raw[cn]).strip() if cn < len(raw) else ''
            if not nombre:
                if meta_by_name:
                    break
                continue
            k = norm(nombre)
            if k.startswith('div') or k.startswith('supervisor') or k.startswith('suma') or k.startswith('diferencia'):
                break
            meta = to_int(raw[ci]) if ci < len(raw) else None
            if meta:
                meta_by_name[norm(canon_name(nombre))] = meta
        if not meta_by_name:
            print(f'⚠️ 🧮GC: la tabla de metas no trae metas para {MESES[hoy.month - 1]}')

# ----------------------------------------------------------------------------- 🧮ING/MAT → matrículas del MES EN CURSO por asesor
# Misma fórmula que la columna "Avance del Mes (Q)" del tablero 🔰GEN, pero siempre con el mes actual (el tablero
# tiene un selector de mes en B6 y, si alguien lo deja en otro mes, mostraría ese mes):
#   COUNTIFS(AE<>"PAGO PENDIENTE", O=asesor, AU=mes, E<>"DEVOLUCIÓN") + COUNTIFS(AE<>"PAGO PENDIENTE", O=asesor, AP=mes, E<>"DEVOLUCIÓN")
MES_ACTUAL = MESES[hoy.month - 1].upper()
INGMAT_COLS = {'E': 'Subestado Funnel', 'O': 'ASESOR', 'AE': 'MES PAGO FILTRO1 CRM', 'AP': 'MES DE PAGO - HYO', 'AU': 'MES PAGO FILTRO2 / RESPUESTA DIV-INGRESANTE'}
ingmat_cols = None
_blk = load_values('ingmat.json', required=False)     # bloque completo 'A1:AW…' (las columnas se ubican por encabezado)
if _blk:
    bh = [norm(x) for x in _blk[0]]
    ingmat_cols = {}
    for letra, encab in INGMAT_COLS.items():
        ci = next((i for i, h in enumerate(bh) if h == norm(encab)), None)
        if ci is None:
            sys.exit(f'🧮ING/MAT: no encuentro la columna "{encab}" en el encabezado')
        ingmat_cols[letra] = [str(r[ci]) if ci < len(r) else '' for r in _blk[1:]]
else:                                                   # respaldo: una columna por archivo (ingmat_E.json, ingmat_O.json, …)
    ingmat_cols = {}
    for letra, encab in INGMAT_COLS.items():
        v = load_values(f'ingmat_{letra}.json', required=False)
        if not v:
            ingmat_cols = None
            break
        hdr = str(v[0][0]) if v and v[0] else ''
        if norm(hdr) != norm(encab):
            sys.exit(f'🧮ING/MAT columna {letra}: encabezado "{hdr}" ≠ "{encab}" (¿se movió la columna en la hoja?)')
        ingmat_cols[letra] = [str(r[0]) if r else '' for r in v[1:]]
mat_mes_calc = None
if ingmat_cols:
    nfilas = max(len(c) for c in ingmat_cols.values())
    _g = lambda c, i: (ingmat_cols[c][i] if i < len(ingmat_cols[c]) else '').strip()
    mat_mes_calc = {}
    for i in range(nfilas):
        if norm(_g('AE', i)) == norm('PAGO PENDIENTE') or norm(_g('E', i)) == norm('DEVOLUCIÓN'):
            continue
        asesor = _g('O', i)
        if not asesor:
            continue
        hit = (norm(_g('AU', i)) == norm(MES_ACTUAL)) + (norm(_g('AP', i)) == norm(MES_ACTUAL))
        if hit:
            k = norm(canon_name(asesor))
            mat_mes_calc[k] = mat_mes_calc.get(k, 0) + hit

# ----------------------------------------------------------------------------- asesores activos
# sync/asesores_activos.json (lo mantiene Claude a pedido de Christopher): solo ellos van en la lista de nombres,
# el ranking, los cuadrantes y la base de los cuartiles de matrículas. Sus evaluaciones pasadas no se tocan.
_act = load_json(os.path.join(SYNC, 'asesores_activos.json'), {}) or {}
ACTIVOS = {norm(canon_name(n)) for n in (_act.get('activos') or []) if str(n).strip()}
ACTIVOS_NOMBRES = [canon_name(n) for n in (_act.get('activos') or []) if str(n).strip()]
def es_activo(name):
    return (not ACTIVOS) or norm(canon_name(name)) in ACTIVOS or norm(name) in ACTIVOS

# ----------------------------------------------------------------------------- 🔰GEN (tablero) → roster, matrículas del mes, % de meta y cuartil por aporte
tablero_by_name = {}
cortes_matriculas = None
mes_tablero = None
gv = load_values('gen.json', required=False)
if gv:
    for r in gv[:12]:
        if r and norm(r[0]) == 'mes' and len(r) > 1 and str(r[1]).strip():
            mes_tablero = str(r[1]).strip().upper()
            break
    if mes_tablero and norm(mes_tablero) != norm(MES_ACTUAL) and mat_mes_calc is None:
        print(f'⚠️ el tablero 🔰GEN está en {mes_tablero} y no hay 🧮ING/MAT para recalcular {MES_ACTUAL}: se omite el avance del tablero')
        gv = None
if gv:
    hi = next((i for i, r in enumerate(gv) if r and norm(r[0]) == 'puesto'), None)
    if hi is not None:
        gh = [norm(x) for x in gv[hi]]
        i_nom = next((i for i, h in enumerate(gh) if h.startswith('usuariodepreinscripcion')), 2)
        i_q = next((i for i, h in enumerate(gh) if h.startswith('avancedelmesq')), 3)
        i_pct = next((i for i, h in enumerate(gh) if h.startswith('avancedelmes') and h != gh[i_q]), 4)
        i_cu = 1
        filas = []
        for raw in gv[hi + 1:]:
            if not raw or len(raw) <= i_nom or not str(raw[i_nom]).strip():
                continue
            if norm(raw[0]).startswith('total'):
                break
            name = canon_name(raw[i_nom])
            if not es_activo(name):
                continue
            q = to_int(raw[i_q]) if i_q < len(raw) else None
            if q is None:
                continue
            m = re.match(r'cuartil\s*(\d)', norm(raw[i_cu]).replace('cuartil', 'cuartil ')) if i_cu < len(raw) else None
            filas.append({'name': name, 'q': q, 'pct': pct(raw[i_pct]) if i_pct < len(raw) else None,
                          'cuartil': ('Q' + m.group(1)) if m else None, 'puesto': to_int(raw[0])})
        if filas and mat_mes_calc is not None:
            # Matrículas del mes en curso calculadas desde 🧮ING/MAT; el tablero solo aporta la lista de asesores.
            # Si el tablero está en el mismo mes, sus cifras deben coincidir (se avisa si no).
            dif = []
            for f in filas:
                q_calc = mat_mes_calc.get(norm(f['name']), 0)
                if mes_tablero and norm(mes_tablero) == norm(MES_ACTUAL) and f['q'] != q_calc:
                    dif.append(f"{f['name']}: tablero {f['q']} ≠ calculado {q_calc}")
                f['q'] = q_calc
                f['pct'] = None          # se recalcula con la meta del mes en curso
                f['cuartil'] = None      # se recalcula sobre el equipo con el mes en curso
                f['puesto'] = None
            if dif:
                print('⚠️ diferencias con el tablero del mismo mes: ' + ' · '.join(dif))
            filas.sort(key=lambda f: (-f['q'], f['name']))
            for i, f in enumerate(filas):
                f['puesto'] = i + 1 if (i == 0 or filas[i - 1]['q'] != f['q']) else filas[i - 1]['puesto']
        if filas:
            qs = [f['q'] for f in filas]
            p75, p50, p25 = quartile_inc(qs, 3), quartile_inc(qs, 2), quartile_inc(qs, 1)
            cortes_matriculas = {'Q1': p75, 'Q2': p50, 'Q3': p25}
            for f in filas:
                if not f['cuartil']:   # mismo criterio QUARTILE.INC de la columna "Cuartil" del tablero
                    f['cuartil'] = 'Q1' if f['q'] >= p75 else 'Q2' if f['q'] >= p50 else 'Q3' if f['q'] >= p25 else 'Q4'
                meta = meta_by_name.get(norm(f['name']))
                if not meta and f['q'] and f['pct']:
                    meta = int(round(f['q'] / (f['pct'] / 100.0)))
                tablero_by_name[norm(f['name'])] = {'mat': f['q'], 'meta': meta, 'avanceMes': f['pct'] if f['pct'] is not None else (round(100.0 * f['q'] / meta, 2) if meta else None),
                                                    'cuartil': f['cuartil'], 'puestoTablero': f['puesto']}


# ----------------------------------------------------------------------------- DASHBOARD (Matriz) → cuartil de calidad, campaña, equipo
dash = load_values('dashboard.json', required=False)
dash_by_name = {}
equipo_sheet = {'mapaCalor': None, 'focoCualitativo': None, 'oportunidad': None, 'distribucionCuartiles': None}
if dash:
    hi = next((i for i, r in enumerate(dash) if r and norm(r[0]) == 'asesor'), None)
    if hi is not None:
        dh = [str(x) for x in dash[hi]]
        idx_score = [i for i, h in enumerate(dh) if norm(h) == 'score']
        idx_cuart = [i for i, h in enumerate(dh) if norm(h) == 'cuartil']
        i_mat = next((i for i, h in enumerate(dh) if norm(h) == 'matriculas'), None)
        i_meta = next((i for i, h in enumerate(dh) if norm(h).startswith('meta')), None)
        i_sede = next((i for i, h in enumerate(dh) if norm(h) == 'sede'), None)
        i_ant = next((i for i, h in enumerate(dh) if norm(h).startswith('antiguedad')), None)
        for raw in dash[hi + 1:]:
            if not raw or not str(raw[0]).strip():
                continue
            g = lambda i: (raw[i] if (i is not None and i < len(raw)) else '')
            name = canon_name(raw[0])
            dash_by_name[norm(name)] = {
                'sede': str(g(i_sede)).strip() or None,
                'antiguedad': str(g(i_ant)).strip() or None,
                'calidadPromedio': ipct(g(idx_score[0])) if idx_score else None,
                'cuartilCalidad': (str(g(idx_cuart[0])).strip().upper() or None) if idx_cuart else None,
                'campana': {
                    'matriculas': to_int(g(i_mat)), 'meta': to_int(g(i_meta)),
                    'avance': pct(g(idx_score[1])) if len(idx_score) > 1 else None,
                    'cuartil': (str(g(idx_cuart[1])).strip().upper() or None) if len(idx_cuart) > 1 else None,
                },
            }
    for i, r in enumerate(dash):
        cells = [str(c) for c in r]
        for j, c in enumerate(cells):
            nc = norm(c)
            if nc.startswith('mapadecalor') and i + 3 < len(dash):
                labels = dash[i + 2][j:j + 4] if len(dash[i + 2]) > j else []
                vals = dash[i + 3][j:j + 4] if len(dash[i + 3]) > j else []
                mc = {}
                for lab, v in zip(labels, vals):
                    k = stage_from_label(lab) or ('paso4' if norm(lab).startswith('cierre') else None)
                    if k and pct(v) is not None:
                        mc[k] = pct(v)
                if len(mc) == 4:
                    equipo_sheet['mapaCalor'] = mc
            if nc.startswith('fococualitativo'):
                m = re.search(r'\n(.+?)\s*\|\s*(\d+)\s*%', c)
                if m:
                    equipo_sheet['focoCualitativo'] = {'paso': stage_from_label(m.group(1)), 'pct': int(m.group(2))}
            if nc.startswith('oportunidad') and '|' in c:
                m = re.search(r'\n(.+?)\s*\|\s*(\d+)\s*%', c)
                if m:
                    equipo_sheet['oportunidad'] = {'paso': stage_from_label(m.group(1)), 'pct': int(m.group(2))}
            if nc == 'cuartil1' and i + 4 < len(dash):
                try:
                    q1 = int(dash[i + 1][j]); q2 = int(dash[i + 1][j + 1])
                    q3 = int(dash[i + 4][j]); q4 = int(dash[i + 4][j + 1])
                    equipo_sheet['distribucionCuartiles'] = {'Q1': q1, 'Q2': q2, 'Q3': q3, 'Q4': q4}
                except Exception:
                    pass


# ----------------------------------------------------------------------------- CHECKLIST manual (opcional)
check_sheet = {}   # norm(asesor) → {item_id: 'ok'|'pendiente'}
cv = load_values('checklist.json', required=False)
if cv and len(cv) >= 2:
    ch = [str(x) for x in cv[0]]
    id_by_col = {}
    for ci, h in enumerate(ch[1:], 1):
        m = re.match(r'\s*P(\d+)\.(\d+)', h, re.I)
        if m:
            id_by_col[ci] = f'paso{m.group(1)}_{m.group(2)}'
    for raw in cv[1:]:
        if not raw or not str(raw[0]).strip():
            continue
        name = canon_name(raw[0])
        d = {}
        for ci, iid in id_by_col.items():
            v = str(raw[ci]).strip().lower() if ci < len(raw) else ''
            if v in ('✓', '✔', 'x', 'si', 'sí', 'true', 'ok', '1'):
                d[iid] = 'ok'
            elif v in ('✗', '✘', 'no', 'false', '0', 'pendiente'):
                d[iid] = 'pendiente'
        if d:
            check_sheet[norm(name)] = d


# ----------------------------------------------------------------------------- historial por asesor (ordenado por fecha)
hist = {}  # canon name → [(date, idx, row)]
for idx, row in enumerate(evaluacion):
    hist.setdefault(row[H_ASESOR], []).append((parse_date(row.get(H_FECHA)), idx, row))
for name, lst in hist.items():
    if all(x[0] for x in lst):
        lst.sort(key=lambda x: (x[0], x[1]))
    # si alguna fecha no se entiende, se respeta el orden del Sheet

def stage_pct(row, k):
    return ipct(row.get(H_STAGE[k]))

def mean_int(vals):
    v = [x for x in vals if x is not None]
    return int(round(sum(v) / len(v))) if v else None

def cuartil_calidad_local(p):
    if p is None: return None
    return 'Q4' if p <= 74 else 'Q3' if p <= 84 else 'Q2' if p <= 94 else 'Q1'

def cuadrante_de(qc, qm):
    if qc not in ('Q1', 'Q2', 'Q3', 'Q4') or qm not in ('Q1', 'Q2', 'Q3', 'Q4'):
        return None
    cal_ok = qc in ('Q1', 'Q2'); mat_ok = qm in ('Q1', 'Q2')
    return 'verde' if (cal_ok and mat_ok) else 'amarillo' if (mat_ok and not cal_ok) else 'naranja' if (cal_ok and not mat_ok) else 'rojo'

def _txt(v):
    s = str(v or '').strip()
    return '' if s in ('', '_', '-', '—') else s

def textos_por_etapa(row):
    """Lee lo que escribió el supervisor y lo reparte por etapa.
    Devuelve (etapas {k: {'fortaleza','oportunidad'}}, fortaleza principal k, brecha principal k, fortalezas general, oportunidades general, evidencia, modo).
    modo = 'etapas' (columnas por etapa del formato nuevo) o 'legado' (un texto general: formato anterior)."""
    evid = _txt(row.get(H_EVID)) if H_EVID else ''
    per = {k: (_txt(row.get(H_FORT_E[k])) if H_FORT_E[k] else '', _txt(row.get(H_OPOR_E[k])) if H_OPOR_E[k] else '') for k in STAGE_KEYS}
    llenas = [k for k in STAGE_KEYS if per[k][0] or per[k][1]]
    fecha = parse_date(row.get(H_FECHA))
    forts_g = _txt(row.get(H_FORTS)) if H_FORTS else ''
    oport_g = _txt(row.get(H_OPORT)) if H_OPORT else ''
    pcts = {k: stage_pct(row, k) for k in STAGE_KEYS}
    con_pct = [k for k in STAGE_KEYS if pcts[k] is not None]
    def mas_baja(cands):
        return min(cands, key=lambda k: (pcts[k] if pcts[k] is not None else 999, STAGE_KEYS.index(k))) if cands else None
    def mas_alta(cands):
        return max(cands, key=lambda k: (pcts[k] if pcts[k] is not None else -1, -STAGE_KEYS.index(k))) if cands else None
    modo = None
    if llenas and (any(k != 'paso4' for k in llenas) or (fecha and fecha >= FECHA_FORMATO_ETAPAS)):
        modo = 'etapas'
    elif llenas == ['paso4']:
        # solo las columnas de "Cerrar" con texto antes del cambio de formato: es el texto general heredado
        forts_g = forts_g or per['paso4'][0]; oport_g = oport_g or per['paso4'][1]
        modo = 'legado'
    elif forts_g or oport_g:
        modo = 'legado'
    fort_k = stage_from_label(row.get(H_FORT_P)) if H_FORT_P else None
    brecha_k = stage_from_label(row.get(H_BRECHA_P)) if H_BRECHA_P else None
    if modo != 'etapas':
        leg = legacy_de(row)
        if leg and (leg['fortalezas'] or leg['oportunidades']):
            forts_g = leg['fortalezas'] or forts_g; oport_g = leg['oportunidades'] or oport_g
            fort_k = fort_k or stage_from_label(leg['fortalezaPrincipal']); brecha_k = brecha_k or stage_from_label(leg['brechaPrincipal'])
            modo = 'legado'
    if modo == 'etapas':
        etapas = {k: {'fortaleza': per[k][0], 'oportunidad': per[k][1]} for k in STAGE_KEYS}
        if not brecha_k or not etapas[brecha_k]['oportunidad']:
            brecha_k = mas_baja([k for k in STAGE_KEYS if per[k][1]] or con_pct)
        if not fort_k or not etapas[fort_k]['fortaleza']:
            fort_k = mas_alta([k for k in STAGE_KEYS if per[k][0]])
    else:
        if oport_g and not brecha_k:
            brecha_k = mas_baja(con_pct)
        if forts_g and not fort_k:
            fort_k = mas_alta([k for k in con_pct if k != brecha_k] or con_pct)
        etapas = {k: {'fortaleza': forts_g if (k == fort_k and forts_g) else '', 'oportunidad': oport_g if (k == brecha_k and oport_g) else ''} for k in STAGE_KEYS}
        if not forts_g: fort_k = None
        if not oport_g: brecha_k = None
    return etapas, fort_k, brecha_k, forts_g, oport_g, evid, modo

def textos_de(row):
    """Textos del supervisor como cadenas (para el hash de la llamada y la lectura del checklist)."""
    etapas, fort_k, brecha_k, forts_g, oport_g, evid, modo = textos_por_etapa(row)
    if modo == 'etapas':
        forts = '\n'.join(f'{STAGE_LABEL[k].capitalize()}: {etapas[k]["fortaleza"]}' for k in STAGE_KEYS if etapas[k]['fortaleza'])
        oport = '\n'.join(f'{STAGE_LABEL[k].capitalize()}: {etapas[k]["oportunidad"]}' for k in STAGE_KEYS if etapas[k]['oportunidad'])
        return forts, oport, evid
    return forts_g, oport_g, evid

def row_base(name, row):
    forts, oport, evid = textos_de(row)
    fecha = str(row.get(H_FECHA, '') or '').strip()
    return sha12(norm(name) + '|' + norm(fecha) + '|' + norm(forts) + '|' + norm(oport) + '|' + norm(evid) + '|' + CHECK_HASH)


# ----------------------------------------------------------------------------- cachés de Claude
casos_cache = load_json(os.path.join(SYNC, 'casos_cache.json'), {})
check_cache = load_json(os.path.join(SYNC, 'checklist_cache.json'), {})
pending = {'casos': [], 'checklist': []}

def caso_base(fortaleza, oportunidad, k):
    """Identifica la observación de una etapa: si cambia el texto del supervisor, cambia la base y se vuelve a redactar."""
    return sha12(norm(fortaleza or '') + '|' + norm(oportunidad or '') + '|' + k)

def leer_checklist_fila(name, row):
    """Estado por ítem de UNA llamada evaluada: desde el caché (lectura de Claude) o todo sin evidencia si no hay texto."""
    forts, oport, evid = textos_de(row)
    base = row_base(name, row)
    fecha = str(row.get(H_FECHA, '') or '').strip()
    if not (forts or oport or evid):
        return {it['id']: {'estado': 'sin_evidencia', 'evidencia': None} for it in CHECK_ITEMS}, base, False
    cc = check_cache.get(base)
    if cc and isinstance(cc.get('items'), dict):
        out = {}
        for it in CHECK_ITEMS:
            e = cc['items'].get(it['id']) or {}
            estado = e.get('estado') if e.get('estado') in ('ok', 'pendiente', 'sin_evidencia') else 'sin_evidencia'
            out[it['id']] = {'estado': estado, 'evidencia': (e.get('evidencia') or '').strip() or None}
        return out, base, False
    etapas, fort_k, brecha_k, _fg, _og, _ev, modo = textos_por_etapa(row)
    pending['checklist'].append({'asesor': name, 'base': base, 'fecha': fecha,
                                 'textos': {'fortalezaPrincipal': fort_k or '', 'brechaPrincipal': brecha_k or '',
                                            'fortalezas': forts, 'oportunidades': oport, 'evidencia': evid,
                                            'porEtapa': etapas, 'modo': modo},
                                 'items': CHECK_ITEMS})
    return None, base, True

analisis = {}
for name, lst in hist.items():
    if not es_activo(name):
        continue
    rows = [x[2] for x in lst]
    last = rows[-1]; prev = rows[-2] if len(rows) > 1 else None
    key = norm(name)
    d = dash_by_name.get(key, {})
    m = mes_by_name.get(key)
    scores = [ipct(r.get(H_SCORE)) for r in rows]
    calidad_prom = d.get('calidadPromedio')
    if calidad_prom is None:
        calidad_prom = mean_int(scores)
    cuartil_cal = d.get('cuartilCalidad') or cuartil_calidad_local(calidad_prom)

    # avance de matrículas del mes → cuartil → cuadrante
    t = tablero_by_name.get(key)
    if AVANCE_MODO == 'tablero' and t:
        m = {'mat': t['mat'], 'meta': t['meta'], 'logroMes': t['avanceMes']}
        avance_mes = t['avanceMes']; avance_ritmo = None
        avance = avance_mes
        cuartil_mat = t['cuartil']
        modo = 'tablero'
    else:
        avance_mes = m['logroMes'] if m else None
        avance_ritmo = None
        if m and m['meta']:
            esperado = m['meta'] * FRACCION_MES
            avance_ritmo = round(100.0 * m['mat'] / esperado, 2) if esperado > 0 else None
        avance = avance_ritmo if AVANCE_MODO == 'ritmo' else avance_mes
        cuartil_mat = cuartil_matriculas(avance)
        modo = 'ritmo' if AVANCE_MODO == 'ritmo' else 'mes'
    cuadrante = cuadrante_de(cuartil_cal, cuartil_mat)

    etapas_last, fort_k, brecha_k, forts, oport, evid, modo_fb = textos_por_etapa(last)

    stages = {}
    for k in STAGE_KEYS:
        p = stage_pct(last, k)
        pp = stage_pct(prev, k) if prev else None
        f_txt = etapas_last[k]['fortaleza'] or None
        o_txt = etapas_last[k]['oportunidad'] or None
        st = {
            'pct': p,
            'prev': pp,
            'delta': (p - pp) if (p is not None and pp is not None) else None,
            'avg': mean_int([stage_pct(r, k) for r in rows]),
            'fortaleza': f_txt, 'oportunidad': o_txt, 'conclusion': None,
            'rol': None, 'texto': None, 'tecnica': None, 'caso': None,
        }
        if o_txt:
            st['rol'] = 'oportunidad'; st['texto'] = o_txt
        elif f_txt:
            st['rol'] = 'fortaleza'; st['texto'] = f_txt
        if f_txt or o_txt:
            base = caso_base(f_txt, o_txt, k)
            c = casos_cache.get(f'{name}|{k}')
            if c and c.get('base') == base:
                st['conclusion'] = _txt(c.get('conclusion')) or None
            if o_txt and c and c.get('base') == base and (c.get('speech') or c.get('dialogo')):
                tec = TEC_BY_ID.get(c.get('tecId')) if c.get('tecId') else None
                st['tecnica'] = ({'id': tec['id'], 'titulo': tec['titulo'], 'ejemplo': (tec.get('ejemplos') or [None])[0]} if tec else None)
                # diálogo tipo WhatsApp (asesor/cliente); el texto para copiar son solo los turnos del asesor
                dialogo = None
                if isinstance(c.get('dialogo'), list) and c.get('tipo', 'speech') != 'accion':
                    dialogo = []
                    for t in c['dialogo']:
                        if not isinstance(t, dict) or not str(t.get('texto', '')).strip():
                            continue
                        quien = 'cliente' if str(t.get('quien', '')).strip().lower().startswith('c') else 'asesor'
                        dialogo.append({'quien': quien, 'texto': str(t['texto']).strip()})
                    if not dialogo:
                        dialogo = None
                texto = '\n\n'.join(t['texto'] for t in dialogo if t['quien'] == 'asesor') if dialogo else c.get('speech', '')
                st['caso'] = {'tipo': c.get('tipo', 'speech'), 'texto': texto or c.get('speech', ''), 'porQue': c.get('porQue', ''), 'base': base, 'dialogo': dialogo}
            if not (c and c.get('base') == base and (st['conclusion'] or not o_txt) and (not o_txt or st['caso'])):
                pending['casos'].append({'asesor': name, 'stage': k, 'base': base, 'pct': p, 'fortaleza': f_txt or '', 'oportunidad': o_txt or '',
                                         'evidencia': evid, 'necesita': 'conclusion+caso' if o_txt else 'conclusion'})
        stages[k] = st

    # checklist de calidad: ventana de las últimas N llamadas evaluadas; manual (Sheet) manda sobre la lectura
    ventana = rows[-VENTANA_CHECKLIST:]
    lecturas = []   # de la más reciente a la más antigua: (fecha, items|None)
    faltan = 0
    for r in reversed(ventana):
        items_r, base_r, pend = leer_checklist_fila(name, r)
        if pend:
            faltan += 1
        lecturas.append((str(r.get(H_FECHA, '') or '').strip(), items_r))
    # lecturas más antiguas ya en caché (fuera de la ventana): solo para saber si un check se "retiró"
    antiguas = []
    for r in reversed(rows[:-VENTANA_CHECKLIST]):
        cc = check_cache.get(row_base(name, r))
        if cc and isinstance(cc.get('items'), dict):
            antiguas.append((str(r.get(H_FECHA, '') or '').strip(), cc['items']))
    items = {}
    for it in CHECK_ITEMS:
        iid = it['id']
        decidido = None
        for fecha_r, items_r in lecturas:
            if not items_r:
                continue
            e = items_r.get(iid) or {}
            if e.get('estado') in ('ok', 'pendiente'):
                decidido = {'estado': e['estado'], 'evidencia': e.get('evidencia'), 'fecha': fecha_r or None}
                break
        if decidido is None:
            decidido = {'estado': 'sin_evidencia', 'evidencia': None, 'fecha': None}
            for fecha_r, items_r in antiguas:
                e = items_r.get(iid) or {}
                if e.get('estado') == 'ok':
                    decidido['retirado'] = True
                    decidido['fechaUltimoOk'] = fecha_r or None
                    break
        items[iid] = decidido
    fuente = 'analisis' if lecturas else None
    manual = check_sheet.get(key)
    if manual:
        for iid, estado in manual.items():
            if iid in items:
                items[iid] = {'estado': estado, 'evidencia': 'Validado por tu supervisor', 'fecha': None, 'fuente': 'sheet'}
        fuente = 'sheet' if fuente is None else fuente + '+sheet'

    analisis[name] = {
        'sede': d.get('sede') or (str(last.get(H_SEDE, '')).strip() if H_SEDE else None) or None,
        'antiguedad': d.get('antiguedad') or (str(last.get(H_ANTIG, '')).strip() if H_ANTIG else None) or None,
        'numEvaluaciones': len(rows),
        'ultimaFecha': str(last.get(H_FECHA, '')).strip() or None,
        'foco': brecha_k, 'fortalezaPrincipal': fort_k, 'modoFeedback': modo_fb,
        'stages': stages,
        'scoreGlobal': last.get(H_SCORE),
        'callScorePct': scores[-1],
        'scorePrev': scores[-2] if len(scores) > 1 else None,
        'scoreDelta': (scores[-1] - scores[-2]) if (len(scores) > 1 and scores[-1] is not None and scores[-2] is not None) else None,
        'cuartilUltima': (str(last.get(H_CUARTIL, '')).strip().upper() or None) if H_CUARTIL else None,
        'calidadPromedio': calidad_prom,
        'cuartilCalidad': cuartil_cal,
        # matrículas del mes (🔰DIA/SEM)
        'matriculas': m['mat'] if m else None,
        'meta': m['meta'] if m else None,
        'avanceMes': avance_mes,
        'avanceRitmo': avance_ritmo,
        'avanceMeta': avance,
        'avanceModo': modo,
        'cuartilMatriculas': cuartil_mat,
        'cuadrante': cuadrante,
        'enMes': m is not None,
        'enMatriz': key in dash_by_name,
        'campana': d.get('campana'),
        'puesto': None, 'totalAsesores': None, 'indice': None,
        'tecnicasDominadas': tec_count.get(key, 0),
        'tecnicasTotal': len(TECNICAS_LIB),
        'checklist': {'fuente': fuente, 'ventana': VENTANA_CHECKLIST, 'evaluacionesLeidas': len([1 for _, x in lecturas if x]),
                      'pendientes': faltan, 'items': items},
    }


# ----------------------------------------------------------------------------- ranking (cuadrante primero; dentro, equilibrio calidad + avance)
def indice_de(a):
    c, m = a.get('calidadPromedio'), a.get('avanceMeta')
    if c is None:
        return None
    return round((c + min(m, AVANCE_TOPE_INDICE)) / 2, 1) if m is not None else float(c)

ranked = []
for name, a in analisis.items():
    a['indice'] = indice_de(a)
    ranked.append(name)
ranked.sort(key=lambda n: (CUADRANTE_RANK.get(analisis[n]['cuadrante'], 9),
                           -(analisis[n]['indice'] if analisis[n]['indice'] is not None else -1),
                           -(analisis[n]['calidadPromedio'] or 0),
                           norm(n)))
for i, n in enumerate(ranked):
    analisis[n]['puesto'] = i + 1
    analisis[n]['totalAsesores'] = len(ranked)


# ----------------------------------------------------------------------------- equipo
mapa_local = {k: round(sum(a['stages'][k]['avg'] for a in analisis.values() if a['stages'][k]['avg'] is not None) /
                       max(1, len([1 for a in analisis.values() if a['stages'][k]['avg'] is not None])), 2) for k in STAGE_KEYS}
mapa = equipo_sheet['mapaCalor'] or mapa_local
foco = equipo_sheet['focoCualitativo'] or {'paso': min(mapa, key=mapa.get), 'pct': int(round(min(mapa.values())))}
opor = equipo_sheet['oportunidad'] or {'paso': max(mapa, key=mapa.get), 'pct': int(round(max(mapa.values())))}
dist = equipo_sheet['distribucionCuartiles'] or {q: len([1 for a in analisis.values() if a['cuartilCalidad'] == q]) for q in ('Q1', 'Q2', 'Q3', 'Q4')}
cuadrantes = {c: [n for n in ranked if analisis[n]['cuadrante'] == c] for c in ('verde', 'amarillo', 'naranja', 'rojo')}
cuadrantes['sinMatriz'] = [n for n in ranked if analisis[n]['cuadrante'] is None]
equipo = {
    'fecha': hoy.strftime('%d/%m/%Y'),
    'mes': MESES[hoy.month - 1],
    'diasHabiles': {'transcurridos': DH_TRANSC, 'total': DH_TOTAL},
    'avanceModo': 'tablero' if tablero_by_name else AVANCE_MODO,
    'cortesMatriculas': cortes_matriculas,
    'fuente': (('🧮ING/MAT (matrículas de ' + MES_ACTUAL.lower() + ', cuartil por aporte en el equipo)' if mat_mes_calc is not None else '🔰GEN tablero (matrículas del mes, cuartil por aporte)') if tablero_by_name else ('🔰DIA/SEM (avance del mes)' if mes_by_name else 'sin avance del mes')) + ' · ' + ('Matriz DASHBOARD (calidad)' if dash else 'sin Matriz'),
    'mesCalculo': MES_ACTUAL.lower(),
    'mesTablero': mes_tablero.lower() if mes_tablero else None,
    'mapaCalor': mapa, 'mapaCalorLocal': mapa_local,
    'focoCualitativo': foco, 'oportunidad': opor,
    'distribucionCuartiles': dist,
    'cuadrantes': cuadrantes,
    'activos': sorted(set(ACTIVOS_NOMBRES) | set(ranked), key=norm) if ACTIVOS else None,
    'totalAsesores': len(ranked),
    'ranking': [{
        'asesor': n, 'puesto': analisis[n]['puesto'], 'sede': analisis[n]['sede'], 'cuadrante': analisis[n]['cuadrante'],
        'calidad': analisis[n]['calidadPromedio'], 'cuartilCalidad': analisis[n]['cuartilCalidad'],
        'matriculas': analisis[n]['matriculas'], 'meta': analisis[n]['meta'], 'avanceMeta': analisis[n]['avanceMeta'], 'avanceMes': analisis[n]['avanceMes'],
        'cuartilMatriculas': analisis[n]['cuartilMatriculas'], 'indice': analisis[n]['indice'],
        'tecnicas': analisis[n]['tecnicasDominadas'], 'evaluaciones': analisis[n]['numEvaluaciones'],
    } for n in ranked],
}


# ----------------------------------------------------------------------------- filas: reparto por etapa listo para la página
TEXTOS_OPORTUNIDAD = []   # (fecha, asesor, texto de oportunidades + evidencia) para los hallazgos de calidad
for row in evaluacion:
    et, fk, bk, _fg, _og, _ev, modo = textos_por_etapa(row)
    row['_etapas'] = {k: {'fortaleza': et[k]['fortaleza'] or None, 'oportunidad': et[k]['oportunidad'] or None} for k in STAGE_KEYS}
    row['_foco'] = bk
    row['_fortalezaPrincipal'] = fk
    row['_modo'] = modo
    _txt_op = ' '.join([et[k]['oportunidad'] or '' for k in STAGE_KEYS] + [_og or '', _ev or ''])
    if _txt_op.strip():
        TEXTOS_OPORTUNIDAD.append((parse_date(row.get("Fecha")), row[H_ASESOR], _txt_op))

# ----------------------------------------------------------------------------- hallazgos de calidad
# Conteo determinista de los temas que más se repiten en las oportunidades que escribe calidad (acumulado y
# últimos 7 días) + la lectura cualitativa de la semana (manías, malas prácticas, buenas prácticas, técnica
# nueva) que la tarea programada escribe en sync/hallazgos_cache.json. La página lo muestra en ⚙️.
TEMAS_HALLAZGOS = [
    ('encuesta', 'No deriva a la encuesta de satisfacción', r'encuesta'),
    ('seguimiento', 'Seguimiento sin día y hora concretos', r'(fecha y hora|hora (especifica|concreta|exacta|definida)|seguimiento (abierto|unicamente)|me avisas|gestion abierta|no genera un proximo|sin agendar|agendamiento|iniciativa del prospecto|retome el contacto|cierres? abiertos?)'),
    ('admision', 'Proceso de admisión incompleto (evaluación, cámara)', r'(proceso de admision|evaluacion de (conocimiento|ingreso)|con camara|prueba de ingreso)'),
    ('parciales', 'Faltan cierres parciales', r'cierres? parcial'),
    ('valor', 'Propuesta de valor débil (sin 2 diferenciales)', r'(diferenciales|propuesta de valor|mas alla del descuento|unicamente en nexus)'),
    ('indagacion', 'Indagación débil: no se conoce la motivación', r'(indagacion|profundiza(r)? (un poco mas )?en (la )?(necesidad|motivacion|decision|situacion|antecedentes)|sondeo debil|responder preguntas|antes de (presentar|desarrollar|argumentar))'),
    ('presentacion', 'Presentación tardía / no usa el nombre del prospecto', r'(presentacion completa|no se presenta|presentarte|tu nombre|nombre (de la|del) prospect|utiliza(r)? el nombre|uso del nombre|saluda al cliente por su nombre)'),
    ('inversion', 'Inversión incompleta (conceptos, seguro)', r'(todos los conceptos|conceptos (correspondientes|que conforman|de inversion|economicos)|seguro contra accidentes|incluyendo el seguro|costos de manera completa|forma clara la inversion|informacion de costos|resumen consolidado de los costos|estructurada la inversion)'),
    ('convalidacion', 'Anticipa % o ciclos de convalidación', r'(anticipar (un |resultados de )?(porcentaje|porcentajes|cantidades|resultados)|adelantar una cantidad|porcentaje de convalidacion|ciclos (de convalidacion|convalidables)|cursos convalidables)'),
    ('erronea', 'Información errónea o confusa', r'(informacion (erronea|incorrecta|poco clara)|argumento falso|error tecnico|confus|inconsistencias|terminologia)'),
    ('tipificacion', 'Tipificación incorrecta', r'tipific'),
]
def _plano(s):   # minúsculas sin tildes, conservando espacios (norm() los quita)
    s = unicodedata.normalize('NFD', str(s or '').lower())
    return re.sub(r'\s+', ' ', ''.join(c for c in s if unicodedata.category(c) != 'Mn'))
def _contar_temas(textos):
    res = []
    for tid, etiqueta, rx in TEMAS_HALLAZGOS:
        hits = [t for t in textos if re.search(rx, _plano(t[2]))]
        if hits:
            res.append({'id': tid, 'tema': etiqueta, 'llamadas': len(hits), 'asesores': len({norm(t[1]) for t in hits}),
                        'nombres': sorted({t[1] for t in hits})})
    return sorted(res, key=lambda r: (-r['llamadas'], -r['asesores']))
_fechas_op = [t[0] for t in TEXTOS_OPORTUNIDAD if t[0]]
_ultima = max(_fechas_op) if _fechas_op else None
_desde7 = (_ultima - datetime.timedelta(days=6)) if _ultima else None
_ult7 = [t for t in TEXTOS_OPORTUNIDAD if t[0] and _desde7 and t[0] >= _desde7]
hallazgos = load_json(os.path.join(SYNC, 'hallazgos_cache.json'), {}) or {}
hallazgos['conteos'] = {
    'acumulado': {'desde': min(_fechas_op).strftime('%d/%m/%Y') if _fechas_op else None, 'hasta': _ultima.strftime('%d/%m/%Y') if _ultima else None,
                  'llamadas': len(TEXTOS_OPORTUNIDAD), 'temas': _contar_temas(TEXTOS_OPORTUNIDAD)},
    'ultimos7': {'desde': _desde7.strftime('%d/%m/%Y') if _desde7 else None, 'hasta': _ultima.strftime('%d/%m/%Y') if _ultima else None,
                 'llamadas': len(_ult7), 'temas': _contar_temas(_ult7)},
}

# ----------------------------------------------------------------------------- salida
out = {
    'version': 8,
    'generado': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'evaluacion': evaluacion,
    'tecnicas': tecnicas,
    'analisis': analisis,
    'equipo': equipo,
    'hallazgos': hallazgos,
}
with open(os.path.join(SYNC, 'pending.json'), 'w', encoding='utf-8') as f:
    json.dump(pending, f, ensure_ascii=False, indent=1)

print(f'asesores: {len(analisis)} · evaluaciones: {len(evaluacion)} · técnicas marcadas: {len(tecnicas)} · con avance del mes: {len([a for a in analisis.values() if a["enMes"]])} · en Matriz: {len([a for a in analisis.values() if a["enMatriz"]])}')
print(f'mes: {equipo["mes"]} · días hábiles {DH_TRANSC}/{DH_TOTAL} · avance por {equipo["avanceModo"]} · cortes de matrículas: {cortes_matriculas}')
print('cuadrantes: ' + ' · '.join(f'{c} {len(v)}' for c, v in cuadrantes.items()))
if UNMATCHED_LOG:
    print('nombres cruzados por parecido / sin cruce:')
    print('\n'.join(sorted(set(UNMATCHED_LOG))))
print(f'PENDIENTES: casos={len(pending["casos"])} checklist={len(pending["checklist"])}  (ver sync/pending.json)')
if DRY:
    print('(modo --dry: data.json no se escribió)')
else:
    # "generado" y la fecha cambian en cada corrida: para comparar con el data.json anterior se ignoran.
    prev_path = os.path.join(ROOT, 'data.json')
    prev = load_json(prev_path, None)
    same = False
    if isinstance(prev, dict):
        a = copy.deepcopy(prev); b = copy.deepcopy(out)
        for x in (a, b):
            x.pop('generado', None)
            if isinstance(x.get('equipo'), dict):
                x['equipo'].pop('fecha', None)
        same = json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)
    if same:
        print('data.json: SIN CAMBIOS (no se reescribe)')
    else:
        with open(prev_path, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print('data.json: ACTUALIZADO')
