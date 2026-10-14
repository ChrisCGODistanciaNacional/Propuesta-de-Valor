#!/usr/bin/env python3
"""Exporta data.json al tablero DIV-PRE (pestaña «Asesor Prueba»).

Lee `data.json` (ya generado por build_data.py) y escribe en `sync/out/tablero/`
un documento JSON por asesor (`a-<slug>.json`) más `equipo.json`. La tarea
programada los sube a la base de datos del artifact del tablero
(colección `calidad`) con la herramienta ArtifactData; el tablero los lee al
abrirse y los cruza con sus propios asesores por nombre.

Uso: python3 sync/export_tablero.py            (desde la raíz del repo)
Salida: lista de archivos escritos y un resumen; no toca data.json ni Sheets.
"""
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'sync', 'out', 'tablero')
STAGES = ['paso1', 'paso2', 'paso3', 'paso4']
STAGE_COLS = ['Ganar confianza\n(15%)', 'Identificar necesidades\n(26%)',
              'Solucionar problemas\n(36%)', 'Cerrar\n(23%)']


def slug(name):
    s = unicodedata.normalize('NFD', str(name or ''))
    s = ''.join(c for c in s if unicodedata.category(c) != 'Mn').lower()
    s = re.sub(r'[^a-z0-9]+', '-', s).strip('-')
    return 'a-' + (s or 'sin-nombre')


def pct_int(v):
    """'72%' / '85,00%' / 85 → 85 (int) o None."""
    if v is None or v == '':
        return None
    t = str(v).strip().replace('%', '').replace(',', '.')
    try:
        return int(round(float(t)))
    except ValueError:
        return None


def fecha_key(s):
    m = re.match(r'^\s*(\d{1,2})/(\d{1,2})/(\d{4})', str(s or ''))
    if not m:
        return None
    return int(m.group(3)) * 10000 + int(m.group(2)) * 100 + int(m.group(1))


def main():
    with open(os.path.join(ROOT, 'data.json'), encoding='utf-8') as f:
        data = json.load(f)
    analisis = data.get('analisis', {})
    equipo = data.get('equipo', {})
    evaluaciones = data.get('evaluacion', [])
    tecnicas = data.get('tecnicas', [])

    # Historial compacto por asesor (una fila por llamada evaluada, en orden de fecha)
    hist = {}
    for e in evaluaciones:
        n = e.get('ASESOR')
        if not n:
            continue
        fecha = e.get('Fecha') or e.get('FECHA DE EVA.') or ''
        row = {
            'f': fecha,
            'k': fecha_key(fecha),
            'p': [pct_int(e.get(c)) for c in STAGE_COLS],
            's': pct_int(e.get('Score cualitativo')),
            'q': (e.get('Cuartil') or '').strip() or None,
            'foco': e.get('_foco'),
        }
        hist.setdefault(n, []).append(row)
    for n in hist:
        hist[n].sort(key=lambda r: (r['k'] or 0))

    # Técnicas dominadas (títulos) por asesor
    tec_by = {}
    for t in tecnicas:
        tec_by.setdefault(t.get('Asesor'), []).append(t.get('Técnica'))

    os.makedirs(OUT, exist_ok=True)
    for old in os.listdir(OUT):
        if old.endswith('.json'):
            os.remove(os.path.join(OUT, old))

    ids = []
    written = []
    for nombre, a in analisis.items():
        doc_id = slug(nombre)
        ids.append(doc_id)
        st = {}
        for k in STAGES:
            s = (a.get('stages') or {}).get(k) or {}
            tec = s.get('tecnica') or None
            caso = s.get('caso') or None
            st[k] = {
                'pct': s.get('pct'), 'prev': s.get('prev'), 'delta': s.get('delta'), 'avg': s.get('avg'),
                'rol': s.get('rol'), 'conclusion': s.get('conclusion'),
                'fortaleza': s.get('fortaleza'), 'oportunidad': s.get('oportunidad'),
                'tecnica': ({'id': tec.get('id'), 'titulo': tec.get('titulo')} if isinstance(tec, dict) else None),
                'caso': ({'tipo': caso.get('tipo'), 'texto': caso.get('texto'), 'porQue': caso.get('porQue')}
                         if isinstance(caso, dict) else None),
            }
        chk = a.get('checklist') or {}
        items = chk.get('items') or {}
        estados = {'ok': 0, 'pendiente': 0, 'sin_evidencia': 0}
        for it in items.values():
            e = (it or {}).get('estado') or 'sin_evidencia'
            estados[e] = estados.get(e, 0) + 1
        doc = {
            'nombre': nombre,
            'sede': a.get('sede'),
            'antiguedad': a.get('antiguedad'),
            'numEvaluaciones': a.get('numEvaluaciones'),
            'ultimaFecha': a.get('ultimaFecha'),
            'foco': a.get('foco'),
            'fortalezaPrincipal': a.get('fortalezaPrincipal'),
            'stages': st,
            'callScorePct': a.get('callScorePct'),
            'scorePrev': a.get('scorePrev'),
            'scoreDelta': a.get('scoreDelta'),
            'cuartilUltima': a.get('cuartilUltima'),
            'calidadPromedio': a.get('calidadPromedio'),
            'cuartilCalidad': a.get('cuartilCalidad'),
            'matriculas': a.get('matriculas'),
            'meta': a.get('meta'),
            'avanceMes': a.get('avanceMes'),
            'cuartilMatriculas': a.get('cuartilMatriculas'),
            'cuadrante': a.get('cuadrante'),
            'enMes': a.get('enMes'),
            'enMatriz': a.get('enMatriz'),
            'campana': a.get('campana'),
            'puesto': a.get('puesto'),
            'totalAsesores': a.get('totalAsesores'),
            'indice': a.get('indice'),
            'tecnicasDominadas': a.get('tecnicasDominadas'),
            'tecnicasTotal': a.get('tecnicasTotal'),
            'tecnicasLista': tec_by.get(nombre, []),
            'checklist': {'fuente': chk.get('fuente'), 'ventana': chk.get('ventana'),
                          'evaluacionesLeidas': chk.get('evaluacionesLeidas'), 'estados': estados,
                          'items': {k: {'estado': (v or {}).get('estado'), 'evidencia': (v or {}).get('evidencia'),
                                        'fecha': (v or {}).get('fecha')} for k, v in items.items()}},
            'historial': [{k2: v2 for k2, v2 in r.items() if k2 != 'k'} for r in hist.get(nombre, [])],
        }
        path = os.path.join(OUT, doc_id + '.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(doc, f, ensure_ascii=False)
        written.append((doc_id, os.path.getsize(path)))

    eq = dict(equipo)
    eq.update({
        'generado': data.get('generado'),
        'version': data.get('version'),
        'exportado': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'asesores': ids,
        'totalEvaluaciones': len(evaluaciones),
    })
    path = os.path.join(OUT, 'equipo.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(eq, f, ensure_ascii=False)
    written.append(('equipo', os.path.getsize(path)))

    with open(os.path.join(OUT, '_index.json'), 'w', encoding='utf-8') as f:
        json.dump({'collection': 'calidad', 'docs': [w[0] for w in written]}, f, ensure_ascii=False, indent=1)

    big = [w for w in written if w[1] > 200 * 1024]
    print('TABLERO: %d documentos en %s' % (len(written), OUT))
    for w in written:
        print('  %-40s %6.1f KB' % (w[0], w[1] / 1024))
    if big:
        print('⚠️ documentos cerca del límite de 256 KB: ' + ', '.join(w[0] for w in big))
    return 0


if __name__ == '__main__':
    sys.exit(main())
