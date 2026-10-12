# Sincronización de `data.json`

La página lee `data.json` (misma carpeta) para mostrar el diagnóstico por asesor. Este folder tiene todo lo que hace falta para regenerarlo; lo corre una tarea programada de Claude (11:00 y 18:00, hora de Lima) con los accesos de Christopher.

## Fuentes

| Archivo de entrada (`sync/inputs/`, no se sube al repo) | Rango que se lee | Spreadsheet |
|---|---|---|
| `feedback.json`  | `'🔰FEEDBACK'!A1:R2000` | Archivo I · `1SJAgbcxpVXpRZ6_Vb4_VW1Pl9BJZMyR8cTH-hmvgdEE` |
| `tecnicas.json`  | `'🏆 TECNICAS'!A1:Z60`  | Archivo I |
| `gen.json`       | `'🔰GEN'!A24:J56`       | Archivo I (tablero: ranking del mes por asesor — matrículas, % de meta y cuartil por aporte) |
| `metas.json`     | `'🧮GC'!B130:J159`      | Archivo I (meta mensual de matrículas por asesor y mes) |
| `diasem.json` (opcional) | `'🔰DIA/SEM'!A19:IF23` | Archivo I (solo respaldo si falta gen.json) |
| `dashboard.json` | `'DASHBOARD'!B5:O90`    | Matriz_CGO_Distancia_2027-I · `1MaiDiBNBicpttRJs2oSAyKvbAD0nI963anN9wfYoeOM` (cuartil de calidad, mapa de calor, focos) |
| `checklist.json` (opcional) | `'✅ CHECKLIST'!A1:AZ80` | Archivo I (validación manual del supervisor) |

Cada archivo es el JSON tal cual lo devuelve el conector de Google Sheets (`{"range": ..., "values": [[...]]}`).

## Pasos

1. Guardar los rangos en `sync/inputs/`.
2. `python3 sync/build_data.py` → escribe `data.json` y `sync/pending.json`.
3. Si `pending.json` trae `casos` o `checklist` pendientes, Claude los escribe en `sync/casos_cache.json` / `sync/checklist_cache.json` (ver formato abajo) y vuelve a correr el paso 2.
4. Commit y push de `data.json` + los cachés.

## Qué calcula `build_data.py`

- Historial por asesor ordenado por fecha (la "última llamada" es la más reciente por fecha, no la última fila).
- Por etapa: `pct` (última), `prev` (anterior), `delta`, `avg` (promedio de todas sus evaluaciones).
- Calidad promedio y cuartil (Q4 ≤ 74 · Q3 75–84 · Q2 85–94 · Q1 ≥ 95) desde la Matriz (o calculado si el asesor no está en ella).
- Matrículas del mes, meta y % de avance desde el tablero 🔰GEN (meta por asesor desde 🧮GC); el cuartil de matrículas es **relativo al equipo** (QUARTILE.INC de las matrículas del mes: Q1 ≥ percentil 75, Q2 ≥ mediana, Q3 ≥ percentil 25, Q4 el resto), igual que la columna "Cuartil" del tablero — `AVANCE_MODO = 'tablero'`. Respaldos con 🔰DIA/SEM: `'ritmo'` (meta prorrateada) o `'mes'` (umbral fijo Q1 ≥ 100 % · Q2 80–99 · Q3 70–79 · Q4 < 70). Cuadrante = cruce de ambos (🟢 verde, 🟡 amarillo, 🟠 naranja, 🔴 rojo); nivel gamificado en la página: 🌱 Promesa › ✨ Talento › ⭐ Estrella › 👑 Leyenda.
- Ranking: primero el cuadrante (🟢 › 🟡 › 🟠 › 🔴), dentro de cada uno el promedio entre calidad y avance (el avance cuenta hasta 150 %).
- Checklist: se leen las últimas 3 llamadas evaluadas de cada asesor (`VENTANA_CHECKLIST`); un ítem queda ✅ si la lectura más reciente que lo menciona dice "ok", 🔸 si dice "pendiente", y ◻️ si ninguna de las 3 lo menciona (si estaba ✅ en una llamada más antigua, se marca como retirado).
- Bloque `equipo`: mapa de calor (pauta), foco cualitativo, oportunidad, distribución por cuartiles, listas por cuadrante, ranking completo.
- Nombres: alias explícitos (`ALIASES`) + cruce por parecido (p. ej. "JOSTYN FLORES SOLANO" ↔ "Justyn Flores Solano"); el nombre canónico es el de 🔰FEEDBACK.

## Reglas de contenido (speeches de caso y textos de la página)

- **Becas**: no se dan en el primer ciclo; desde el 2.º ciclo son concursables (semibeca por excelencia) solo si el alumno mantiene buen promedio. Nunca abrir ni preguntar por becas como gancho ("¿buscas una beca?") ni ofrecerlas como beneficio del primer ciclo: si el prospecto las menciona, responder con claridad y como motivación ("desde el segundo ciclo, si mantienes buen promedio, concursas a una semibeca"); si no, no tocar el tema.
- Nunca inventar montos, descuentos, fechas concretas ni beneficios: solo datos de la observación del supervisor o reglas generales presentes en `index.html`.

## Formato de los cachés

`casos_cache.json` (clave: nombre del asesor):
```json
{"stage": "paso3", "tecId": "tec_08", "tipo": "speech", "porQue": "...", "speech": "...", "base": "<12 hex>"}
```
`checklist_cache.json` (clave: `base` de la LLAMADA evaluada = hash de asesor + fecha + textos):
```json
{"asesor": "Diana Carol Taipe Rojas", "fecha": "06/10/2026", "items": {"paso1_1": {"estado": "ok|pendiente|sin_evidencia", "evidencia": "..." }}}
```
`base` lo entrega `pending.json`; si la evaluación cambia, cambia la `base` y esa llamada vuelve a quedar pendiente. Las llamadas sin texto del supervisor no se piden: quedan sin evidencia.
