# -*- coding: utf-8 -*-
"""Patrones del historial por etapa, v2 (20/11/2026, Christopher).

Qué cambia frente a la v1:
- Cada etapa tiene sus propias «señales»: frases concretas de ESE paso (p. ej. en Identificar
  necesidades: «no pregunta el porqué», «salta al precio»), no temas generales.
- Se separa claramente lo que repite MAL de lo que repite BIEN, y se marca dónde se concentra
  (la señal mala más repetida).
- Lo que calidad anotó en la columna de la etapa pero pertenece a otro paso (inversión, admisión…)
  se muestra aparte, en una línea, para no mezclarlo.
- El total es el número de auditorías con texto EN ESA etapa (no todas sus auditorías).
- Cada señal trae las fechas en que apareció y la frase de calidad más reciente que la respalda.
"""
import re
import unicodedata

def plano(t):
    t = unicodedata.normalize('NFD', str(t or '').lower())
    return ''.join(c for c in t if unicodedata.category(c) != 'Mn')

def solo_etapa(t):
    """Calidad a veces pega en la columna de la etapa secciones de otra («3. Solución de problemas…»): se corta ahí."""
    t = str(t or '')
    m = re.search(r'\n\s*[234]\.\s+[A-ZÁÉÍÓÚ]', t)
    return t[:m.start()] if m else t

def frases(t):
    t = re.sub(r'\s*\n\s*-\s*', '\n', solo_etapa(t))
    return [f.strip(' -•\n') for f in re.split(r'(?<=[.!?])\s+|\n', t) if len(f.strip(' -•\n')) > 12]

# Señales por etapa: (id, etiqueta corta y accionable, regex sobre el texto sin tildes)
SENALES = {
    'paso2': {
        'mal': [
            ('porque', 'No pregunta el «para qué»: por qué esa carrera y qué quiere lograr',
             r'(por que (el prospecto |la prospecta |\w+ )?(quiere|desea|prefiere)|motivo|motiva|objetivos profesionales|expectativas|que busca|que espera|razones)'),
            ('precio', 'Salta al precio o al descuento antes de conocer la necesidad',
             r'(descuento|unicamente en (el |los )?(costo|descuento|precio)|solo del precio|solo en la inversion|inversion economica|basada? unicamente en el costo)'),
            ('situacion', 'No profundiza su situación: trabajo, horarios, disponibilidad',
             r'(horario|disponibilidad|situacion (laboral|actual|academica)|a que se dedica|donde y cuanto tiempo)'),
            ('repregunta', 'No repregunta lo que el cliente deja caer (postergar, acortar, comparar)',
             r'(prefiere matricularse|postergacion|reducir la duracion|compara(r)? universidades|analizara|segunda carrera|evaluar con mayor)'),
            ('conecta', 'No conecta lo que descubrió con 2 diferenciales',
             r'(dos diferenciales|diferenciales (academicos|institucionales|de la universidad|vinculados)|conecta los beneficios)'),
        ],
        'bien': [
            ('datos', 'Identifica dónde vive, si trabaja y qué estudió antes',
             r'(identificas que|reside|trabaja actualmente|estudios previos|situacion academica|esta evaluando)'),
            ('presupuesto', 'Pregunta por su presupuesto o posibilidades',
             r'(presupuesto|posibilidades economicas)'),
            ('convalida', 'Explora estudios previos para convalidar',
             r'(intencion de convalidar|necesidades de convalidacion|convalid)'),
            ('motivo_ok', 'Descubre la motivación del cliente',
             r'(identificas (que|su) (motiv|objetivo)|conociste (su|el) motivo)'),
        ],
        'otro': [
            ('admision', 'Proceso de admisión', r'(proceso de admision|evaluacion de admision|en que consiste la evaluacion|requisitos)'),
            ('inversion', 'Inversión y cuotas', r'(primera cuota|pension|calculaste|matricula|seguro|examen medico)'),
            ('modalidad', 'Modalidad y horarios de clase', r'(proceso es virtual|horario de las clases|modalidades disponibles)'),
        ],
    },
}

def _match(rx, texto):
    for f in frases(texto):
        if re.search(rx, plano(f)):
            return f
    return None

def patrones_etapa(k, items, minimo=2):
    """items: [(fecha, fortaleza, oportunidad)] en orden cronológico, solo auditorías con texto en la etapa k.
    Devuelve None si la etapa no tiene señales propias (se usa la v1)."""
    cfg = SENALES.get(k)
    if not cfg or not items:
        return None
    n = len(items)
    def cuenta(lst, campos):
        out = []
        for sid, lbl, rx in lst:
            fechas, frase = [], None
            for i, (fecha, fo, op) in enumerate(items):
                hit = None
                for txt in campos(fo, op):
                    hit = hit or _match(rx, txt)
                if hit:
                    fechas.append(fecha); frase = hit
            if len(fechas) >= minimo:
                out.append({'id': sid, 'tema': lbl, 'n': len(fechas), 'ultima': bool(fechas) and fechas[-1] == items[-1][0],
                            'fechas': fechas[-4:], 'frase': (frase[:180] + '…') if frase and len(frase) > 180 else frase})
        return sorted(out, key=lambda x: (-x['n'], not x['ultima']))
    mal = cuenta(cfg['mal'], lambda fo, op: [op])
    bien = cuenta(cfg['bien'], lambda fo, op: [fo])
    otro = cuenta(cfg['otro'], lambda fo, op: [fo, op])
    return {'v': 2, 'total': n, 'mejorar': mal[:5], 'bien': bien[:3],
            'otroPaso': [{'tema': x['tema'], 'n': x['n']} for x in otro],
            'foco': mal[0]['id'] if mal else None}
