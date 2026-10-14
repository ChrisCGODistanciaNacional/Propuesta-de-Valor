# Sincronización de `data.json`

La página lee `data.json` (misma carpeta) para mostrar el diagnóstico por asesor. Este folder tiene todo lo que hace falta para regenerarlo; lo corre una tarea programada de Claude (11:00 y 18:00, hora de Lima) con los accesos de Christopher.

## Fuentes

| Archivo de entrada (`sync/inputs/`, no se sube al repo) | Rango que se lee | Spreadsheet |
|---|---|---|
| `feedback.json`  | `'🔰FEEDBACK'!A1:AB2000` | Archivo I · `1SJAgbcxpVXpRZ6_Vb4_VW1Pl9BJZMyR8cTH-hmvgdEE` (desde el 12/10/2026 trae una columna de Fortaleza y una de Oportunidades por etapa) |
| `tecnicas.json`  | `'🏆 TECNICAS'!A1:Z60`  | Archivo I |
| `gen.json`       | `'🔰GEN'!A1:J56`        | Archivo I (tablero: lista de asesores del ranking; su mes en B6 solo se usa para avisar) |
| `ingmat.json`    | `'🧮ING/MAT'!A1:AW3000` | Archivo I (ingresantes/matriculados: matrículas del **mes en curso** por asesor con la misma fórmula del tablero — AE≠"PAGO PENDIENTE", E≠"DEVOLUCIÓN", AU=mes o AP=mes — pero siempre con el mes actual, aunque el tablero esté en otro mes) |
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

## Formato de 🔰FEEDBACK (desde el 12/10/2026)

- Columnas por etapa: `Ganar confianza (15%) - Fortaleza`, `… - Oportunidades` (y lo mismo para Identificar necesidades, Solucionar problemas y Cerrar), más `FECHA DE EVA.` (fecha de la evaluación). Ya no se usan `Fortaleza principal`, `Brecha principal`, `FORTALEZAS` ni `OPORTUNIDADES`.
- **Regla para el área de calidad**: cada observación va en la columna de SU etapa; el comentario general va en `Evidencia / observación`. Una evaluación con texto solo en las columnas de "Cerrar" se lee como observación de Cerrar.
- Evaluaciones anteriores al cambio: sus textos generales quedaron en las columnas de "Cerrar"; el script las reconoce (fecha anterior al 13/10/2026 y solo "Cerrar" con texto) y usa `sync/legacy_feedback.json` (copia del formato anterior) para recuperar la fortaleza y brecha principal que etiquetó el supervisor.
- Por asesor y etapa el script deja `fortaleza`, `oportunidad`, `conclusion` (síntesis escrita por Claude para "Lo que observó tu supervisor"), `foco` (brecha principal: la etapa con oportunidad de menor %) y `fortalezaPrincipal`.

## Qué calcula `build_data.py`

- Historial por asesor ordenado por fecha (la "última llamada" es la más reciente por fecha, no la última fila).
- Por etapa: `pct` (última), `prev` (anterior), `delta`, `avg` (promedio de todas sus evaluaciones).
- Calidad promedio y cuartil (Q4 ≤ 74 · Q3 75–84 · Q2 85–94 · Q1 ≥ 95) desde la Matriz (o calculado si el asesor no está en ella).
- Matrículas del mes en curso contadas desde 🧮ING/MAT (el tablero 🔰GEN solo da la lista de asesores; si su selector de mes quedó en otro mes, no afecta), meta por asesor desde 🧮GC; el cuartil de matrículas es **relativo al equipo** (QUARTILE.INC de las matrículas del mes: Q1 ≥ percentil 75, Q2 ≥ mediana, Q3 ≥ percentil 25, Q4 el resto), igual que la columna "Cuartil" del tablero — `AVANCE_MODO = 'tablero'`. Respaldos con 🔰DIA/SEM: `'ritmo'` (meta prorrateada) o `'mes'` (umbral fijo Q1 ≥ 100 % · Q2 80–99 · Q3 70–79 · Q4 < 70). Cuadrante = cruce de ambos (🟢 verde, 🟡 amarillo, 🟠 naranja, 🔴 rojo); nivel gamificado en la página: 🌱 Promesa › ✨ Talento › ⭐ Estrella › 👑 Leyenda.
- Ranking: primero el cuadrante (🟢 › 🟡 › 🟠 › 🔴), dentro de cada uno el promedio entre calidad y avance (el avance cuenta hasta 150 %).
- Checklist: se leen las últimas 3 llamadas evaluadas de cada asesor (`VENTANA_CHECKLIST`); un ítem queda ✅ si la lectura más reciente que lo menciona dice "ok", 🔸 si dice "pendiente", y ◻️ si ninguna de las 3 lo menciona (si estaba ✅ en una llamada más antigua, se marca como retirado).
- Bloque `equipo`: mapa de calor (pauta), foco cualitativo, oportunidad, distribución por cuartiles, listas por cuadrante, ranking completo.
- Nombres: alias explícitos (`ALIASES`) + cruce por parecido (p. ej. "JOSTYN FLORES SOLANO" ↔ "Justyn Flores Solano"); el nombre canónico es el de 🔰FEEDBACK.

## Reglas de contenido (speeches de caso y textos de la página)

- **Becas**: no se dan en el primer ciclo; desde el 2.º ciclo son concursables (semibeca por excelencia) solo si el alumno mantiene buen promedio. Nunca abrir ni preguntar por becas como gancho ("¿buscas una beca?") ni ofrecerlas como beneficio del primer ciclo: si el prospecto las menciona, responder con claridad y como motivación ("desde el segundo ciclo, si mantienes buen promedio, concursas a una semibeca"); si no, no tocar el tema.
- Nunca inventar montos, descuentos, fechas concretas ni beneficios: solo datos de la observación del supervisor o reglas generales presentes en `index.html`.

## Formato de los cachés

`casos_cache.json` (clave: `"<nombre del asesor>|<stage>"`, una entrada por etapa con texto del supervisor):
```json
{"stage": "paso3", "base": "<12 hex>", "conclusion": "síntesis de 1–2 frases de lo que observó el supervisor en esa etapa",
 "tecId": "tec_08", "tipo": "speech", "porQue": "...", "speech": "...",
 "dialogo": [{"quien": "asesor", "texto": "..."}, {"quien": "cliente", "texto": "..."}, {"quien": "asesor", "texto": "..."}]}
```
Toda etapa con texto necesita `conclusion`; `tecId/porQue/dialogo/speech` solo cuando la etapa tiene oportunidad (`pending.casos[].necesita` = `conclusion` o `conclusion+caso`).
`dialogo` es la conversación de ejemplo tipo WhatsApp (asesor a la izquierda, cliente a la derecha): 3 turnos del asesor (los párrafos del speech) con 2–3 respuestas cortas y realistas del cliente entre medio, coherentes con la observación del supervisor. `speech` = solo los turnos del asesor unidos por línea en blanco (es lo que copia el botón). Para `tipo: "accion"` no hay diálogo.
`checklist_cache.json` (clave: `base` de la LLAMADA evaluada = hash de asesor + fecha + textos):
```json
{"asesor": "Diana Carol Taipe Rojas", "fecha": "06/10/2026", "items": {"paso1_1": {"estado": "ok|pendiente|sin_evidencia", "evidencia": "..." }}}
```
`base` lo entrega `pending.json`; si la evaluación cambia, cambia la `base` y esa llamada (o etapa) vuelve a quedar pendiente. Las llamadas sin texto del supervisor no se piden: quedan sin evidencia.

## Tablero DIV-PRE · pestaña «Asesor Prueba» (desde el 14/10/2026)

`python3 sync/export_tablero.py` (después de `build_data.py`) reduce `data.json` a un documento por asesor (`sync/out/tablero/a-<slug>.json`, con etapas, foco, conclusiones, técnica, checklist e historial de evaluaciones) más `equipo.json`, y deja la lista en `sync/out/tablero/_index.json`. La tarea programada los sube con la herramienta ArtifactData (`action: batch`, `op: set`, colección `calidad`, `file_path` de cada archivo) a la base de datos del tablero `https://claude.ai/artifact/A6ZRmsZQJsjgBsE6wKNSXk`. El tablero cruza cada documento con sus asesores por nombre. `sync/out/` no se sube al repo.
