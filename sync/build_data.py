#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arma data.json para la página "Propuesta de Valor" (CGO Distancia) a partir de:

  sync/inputs/feedback.json   ← '🔰FEEDBACK'!A1:R2000   (archivo I, spreadsheetId 1SJAgbcxpVXpRZ6_Vb4_VW1Pl9BJZMyR8cTH-hmvgdEE)
  sync/inputs/tecnicas.json   ← '🏆 TECNICAS'!A1:Z60    (archivo I)
  sync/inputs/diasem.json     ← '🔰DIA/SEM'!A19:IF23    (archivo I: avance del mes por asesor y su meta de matrículas)
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
#   'ritmo' → matrículas del mes / meta prorrateada a los días hábiles transcurridos (¿vas al ritmo del mes?)
#   'mes'   → matrículas del mes / meta completa del mes (el "Logro %" de 🔰DIA/SEM)
AVANCE_MODO = 'ritmo'
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
    cands = [STAGE_LABEL[k]] + STAGE_LABEL_ALT.get(k, [])
    for h in headers:
        nh = norm(h)
        if any(nh.startswith(norm(c)) for c in cands):
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
H_FECHA = col(headers, 'fecha')
H_SCORE = col(headers, 'score cualitativo', 'score')
H_CUARTIL = col(headers, 'cuartil')
H_SEDE = col(headers, 'sede')
H_ANTIG = col(headers, 'tramo antiguedad', 'antiguedad')
H_FORT_P = col(headers, 'fortaleza principal')
H_BRECHA_P = col(headers, 'brecha principal')
H_FORTS = col(headers, 'fortalezas')
H_OPORT = col(headers, 'oportunidades')
H_EVID = col(headers, 'evidencia / observacion', 'evidencia')
H_STAGE = {k: stage_col(headers, k) for k in STAGE_KEYS}
missing = [k for k, v in H_STAGE.items() if not v]
if not H_ASESOR or missing:
    sys.exit(f'🔰FEEDBACK: no encuentro columnas clave (asesor={H_ASESOR}, etapas faltantes={missing})')

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
    evaluacion.append(row)

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

def textos_de(row):
    forts = str(row.get(H_FORTS, '') or '').strip() if H_FORTS else ''
    oport = str(row.get(H_OPORT, '') or '').strip() if H_OPORT else ''
    evid = str(row.get(H_EVID, '') or '').strip() if H_EVID else ''
    blank = lambda s: s in ('', '_', '-', '—')
    return ('' if blank(forts) else forts), ('' if blank(oport) else oport), ('' if blank(evid) else evid)

def row_base(name, row):
    forts, oport, evid = textos_de(row)
    fecha = str(row.get(H_FECHA, '') or '').strip()
    return sha12(norm(name) + '|' + norm(fecha) + '|' + norm(forts) + '|' + norm(oport) + '|' + norm(evid) + '|' + CHECK_HASH)


# ----------------------------------------------------------------------------- cachés de Claude
casos_cache = load_json(os.path.join(SYNC, 'casos_cache.json'), {})
check_cache = load_json(os.path.join(SYNC, 'checklist_cache.json'), {})
pending = {'casos': [], 'checklist': []}

def caso_base(texto, k):
    return sha12(norm(texto) + '|' + k)

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
    pending['checklist'].append({'asesor': name, 'base': base, 'fecha': fecha,
                                 'textos': {'fortalezaPrincipal': str(row.get(H_FORT_P, '') or ''), 'brechaPrincipal': str(row.get(H_BRECHA_P, '') or ''),
                                            'fortalezas': forts, 'oportunidades': oport, 'evidencia': evid},
                                 'items': CHECK_ITEMS})
    return None, base, True

analisis = {}
for name, lst in hist.items():
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

    # avance de matrículas del mes (🔰DIA/SEM) → cuartil → cuadrante
    avance_mes = m['logroMes'] if m else None
    avance_ritmo = None
    if m and m['meta']:
        esperado = m['meta'] * FRACCION_MES
        avance_ritmo = round(100.0 * m['mat'] / esperado, 2) if esperado > 0 else None
    avance = avance_ritmo if AVANCE_MODO == 'ritmo' else avance_mes
    cuartil_mat = cuartil_matriculas(avance)
    cuadrante = cuadrante_de(cuartil_cal, cuartil_mat)

    fort_k = stage_from_label(last.get(H_FORT_P)) if H_FORT_P else None
    brecha_k = stage_from_label(last.get(H_BRECHA_P)) if H_BRECHA_P else None
    forts, oport, evid = textos_de(last)

    stages = {}
    for k in STAGE_KEYS:
        p = stage_pct(last, k)
        pp = stage_pct(prev, k) if prev else None
        st = {
            'pct': p,
            'prev': pp,
            'delta': (p - pp) if (p is not None and pp is not None) else None,
            'avg': mean_int([stage_pct(r, k) for r in rows]),
            'rol': None, 'texto': None, 'tecnica': None, 'caso': None,
        }
        if k == fort_k and forts:
            st['rol'] = 'fortaleza'; st['texto'] = forts
        elif k == brecha_k and oport:
            st['rol'] = 'oportunidad'; st['texto'] = oport
            base = caso_base(oport, k)
            c = casos_cache.get(name)
            if c and c.get('base') == base and c.get('stage') == k:
                tec = TEC_BY_ID.get(c.get('tecId')) if c.get('tecId') else None
                st['tecnica'] = ({'id': tec['id'], 'titulo': tec['titulo'], 'ejemplo': (tec.get('ejemplos') or [None])[0]} if tec else None)
                st['caso'] = {'tipo': c.get('tipo', 'speech'), 'texto': c['speech'], 'porQue': c.get('porQue', ''), 'base': base}
            else:
                pending['casos'].append({'asesor': name, 'stage': k, 'base': base, 'oportunidades': oport, 'fortalezas': forts, 'evidencia': evid})
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
        'avanceModo': AVANCE_MODO,
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
MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre']
equipo = {
    'fecha': hoy.strftime('%d/%m/%Y'),
    'mes': MESES[hoy.month - 1],
    'diasHabiles': {'transcurridos': DH_TRANSC, 'total': DH_TOTAL},
    'avanceModo': AVANCE_MODO,
    'fuente': ('🔰DIA/SEM (avance del mes)' if mes_by_name else 'sin 🔰DIA/SEM') + ' · ' + ('Matriz DASHBOARD (calidad)' if dash else 'sin Matriz'),
    'mapaCalor': mapa, 'mapaCalorLocal': mapa_local,
    'focoCualitativo': foco, 'oportunidad': opor,
    'distribucionCuartiles': dist,
    'cuadrantes': cuadrantes,
    'totalAsesores': len(ranked),
    'ranking': [{
        'asesor': n, 'puesto': analisis[n]['puesto'], 'sede': analisis[n]['sede'], 'cuadrante': analisis[n]['cuadrante'],
        'calidad': analisis[n]['calidadPromedio'], 'cuartilCalidad': analisis[n]['cuartilCalidad'],
        'matriculas': analisis[n]['matriculas'], 'meta': analisis[n]['meta'], 'avanceMeta': analisis[n]['avanceMeta'], 'avanceMes': analisis[n]['avanceMes'],
        'cuartilMatriculas': analisis[n]['cuartilMatriculas'], 'indice': analisis[n]['indice'],
        'tecnicas': analisis[n]['tecnicasDominadas'], 'evaluaciones': analisis[n]['numEvaluaciones'],
    } for n in ranked],
}


# ----------------------------------------------------------------------------- salida
out = {
    'version': 7,
    'generado': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
    'evaluacion': evaluacion,
    'tecnicas': tecnicas,
    'analisis': analisis,
    'equipo': equipo,
}
with open(os.path.join(SYNC, 'pending.json'), 'w', encoding='utf-8') as f:
    json.dump(pending, f, ensure_ascii=False, indent=1)

print(f'asesores: {len(analisis)} · evaluaciones: {len(evaluacion)} · técnicas marcadas: {len(tecnicas)} · en 🔰DIA/SEM: {len([a for a in analisis.values() if a["enMes"]])} · en Matriz: {len([a for a in analisis.values() if a["enMatriz"]])}')
print(f'mes: {equipo["mes"]} · días hábiles {DH_TRANSC}/{DH_TOTAL} · avance por {AVANCE_MODO}')
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
