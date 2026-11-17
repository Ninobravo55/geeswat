# -*- coding: utf-8 -*-
"""
caudal_qc_relleno.py — geeSWAT
Control de calidad y relleno de vacíos de series diarias de caudal
observado (SENAMHI u otra fuente con formato Date,Flow).

Envuelve la lógica pura de caudal_qc_core.py como QgsProcessingAlgorithm,
siguiendo el mismo patrón que mde_descargar_mde.py.

geeSWAT v1.2.3 — Geomatica Ambiental
"""
import os

from qgis.PyQt.QtGui import QIcon
from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingParameterFile,
    QgsProcessingParameterString,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterBoolean,
)

from . import caudal_qc_core as qc


class CaudalQCRelleno(QgsProcessingAlgorithm):
    """Control de calidad + relleno de vacíos de caudal observado."""

    INPUT = 'INPUT'
    FORMATO_ENTRADA = 'FORMATO_ENTRADA'
    COL_FECHA = 'COL_FECHA'
    COL_FLOW = 'COL_FLOW'
    FORMATO_FECHA = 'FORMATO_FECHA'
    HOJA_EXCEL = 'HOJA_EXCEL'
    MES_INICIO_ANIO_HIDRO = 'MES_INICIO_ANIO_HIDRO'
    METODO = 'METODO'
    UMBRAL_ADVERTENCIA = 'UMBRAL_ADVERTENCIA'
    UMBRAL_RACHA = 'UMBRAL_RACHA'
    Z_UMBRAL = 'Z_UMBRAL'
    K_SIGMA_SALTO = 'K_SIGMA_SALTO'
    REINDEXAR = 'REINDEXAR'
    OUTPUT_CSV = 'OUTPUT_CSV'
    OUTPUT_REPORTE = 'OUTPUT_REPORTE'

    FORMATOS_ENTRADA_LABEL = [
        'CSV largo (columnas Date, Flow — una fila por día)',
        'Excel ancho (SENAMHI/ANA — una columna por año hidrológico)',
    ]
    FORMATOS_ENTRADA_KEY = ['csv_largo', 'excel_ancho']

    METODOS_LABEL = [
        'Log-lineal (recomendado — consistente con recesión exponencial de caudal)',
        'Lineal',
        'Spline cúbica local (con respaldo automático a log-lineal si oscila)',
    ]
    METODOS_KEY = ['loglineal', 'lineal', 'spline']

    FORMATOS_FECHA_LABEL = ['mm/dd/aaaa', 'dd/mm/aaaa', 'aaaa-mm-dd']
    FORMATOS_FECHA_KEY = ['%m/%d/%Y', '%d/%m/%Y', '%Y-%m-%d']

    def createInstance(self):
        return CaudalQCRelleno()

    def name(self):
        return 'caudal_qc_relleno'

    def displayName(self):
        return 'Caudal observado — Control de calidad y relleno de vacíos'

    def group(self):
        return 'Hidrología'

    def groupId(self):
        return 'hidrologia_geeswat'

    def tags(self):
        return [
            'caudal', 'flow', 'senamhi', 'hidrologia', 'swat', 'qc',
            'control de calidad', 'relleno', 'gaps', 'interpolacion',
            'observado', 'calibracion', 'validacion',
        ]

    def icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                                  'Icons', 'dem.png')
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return QIcon()

    def shortHelpString(self):
        return """
<b>Control de calidad y relleno de vacíos — Caudal observado (geeSWAT)</b><br><br>

Lee un CSV de caudal diario (columnas <b>Date</b> y <b>Flow</b> por defecto),
ejecuta un diagnóstico de calidad y rellena únicamente los valores
faltantes/no numéricos (p. ej. <code>#N/D</code>). No modifica valores medidos.<br><br>

<b>Formatos de entrada admitidos:</b><br>
<ul>
<li><b>CSV largo</b>: columnas <code>Date, Flow</code>, una fila por día (como el
export típico de SENAMHI en serie continua).</li>
<li><b>Excel ancho</b>: primera columna con la plantilla día/mes del año
hidrológico y una columna por año (encabezado con 4 dígitos de año, p. ej.
"2009-2010"). Si una celda cae en 29-feb de un año no bisiesto se omite
puntualmente y queda avisado en el log — no detiene el proceso completo.
Requiere <code>openpyxl</code> instalado.</li>
</ul>

<b>Diagnóstico adicional — saltos bruscos día a día:</b> además del z-score
mensual (nivel inusual para la época) y las rachas planas, se reporta
cualquier cambio |Flow(t)-Flow(t-1)| que exceda la media + k·desviación
estándar de los incrementos típicos de la serie — pensado para detectar un
salto puntual de sensor o un dato mal tecleado que el z-score mensual
podría no marcar si cae dentro del rango normal de esa temporada.<br><br>

<b>Lo que SÍ corrige automáticamente:</b><br>
<ul>
<li>Valores vacíos o marcados como no disponibles (interpolación).</li>
<li>Huecos de calendario si faltan fechas completas (con la opción de reindexar).</li>
</ul>

<b>Lo que SOLO reporta (no corrige solo, por criterio hidrológico):</b><br>
<ul>
<li>Rachas de valores idénticos prolongadas (posible dato "pegado" o estiaje real).</li>
<li>Outliers estadísticos (z-score de log(Flow) por mes).</li>
<li>Saltos bruscos día a día.</li>
<li>Valores negativos o cero.</li>
</ul>

<b>Métodos de interpolación disponibles:</b><br>
<ul>
<li><b>Log-lineal</b>: interpola en log(Flow); más consistente con la
recesión exponencial del caudal que la interpolación lineal simple.</li>
<li><b>Lineal</b>: interpolación directa entre el valor anterior y siguiente.</li>
<li><b>Spline cúbica local</b>: ajusta un spline sobre una ventana de puntos
medidos cercanos al hueco. Si el spline oscila (valores negativos), ese
episodio específico cae automáticamente a log-lineal y queda marcado en
el reporte — un spline global sobre miles de datos diarios de caudal es
numéricamente inestable, por eso se usa ventana local con respaldo.</li>
</ul>

<b>Gaps largos:</b> se rellenan igual que los cortos, pero si un episodio
supera el umbral configurado se marca con advertencia explícita en el
reporte — la interpolación de gaps largos en temporada de avenidas puede
no capturar picos reales de crecida.<br><br>

<b>Salida:</b> CSV corregido (solo columnas Date,Flow, listo para SWAT) +
reporte de texto QC por separado con el detalle de lo rellenado, las
rachas sospechosas y los outliers detectados.
"""

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFile(
            self.INPUT,
            'Archivo de caudal observado (.csv o .xlsx según formato elegido abajo)'
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.FORMATO_ENTRADA, 'Formato del archivo de entrada',
            options=self.FORMATOS_ENTRADA_LABEL, defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterString(
            self.COL_FECHA, 'Nombre de columna de fecha (solo CSV largo)',
            defaultValue='Date', optional=True
        ))
        self.addParameter(QgsProcessingParameterString(
            self.COL_FLOW, 'Nombre de columna de caudal (solo CSV largo)',
            defaultValue='Flow', optional=True
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.FORMATO_FECHA, 'Formato de fecha en el CSV de entrada (solo CSV largo)',
            options=self.FORMATOS_FECHA_LABEL, defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterString(
            self.HOJA_EXCEL, 'Nombre de hoja de Excel (solo Excel ancho — vacío = primera hoja)',
            defaultValue='', optional=True
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.MES_INICIO_ANIO_HIDRO,
            'Mes de inicio del año hidrológico (solo Excel ancho — Perú: 10 = octubre)',
            type=QgsProcessingParameterNumber.Type.Integer,
            defaultValue=10, minValue=1, maxValue=12
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.METODO, 'Método de interpolación para rellenar vacíos',
            options=self.METODOS_LABEL, defaultValue=0
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.UMBRAL_ADVERTENCIA,
            'Días de gap a partir de los cuales se emite advertencia (se rellena igual)',
            type=QgsProcessingParameterNumber.Type.Integer,
            defaultValue=7, minValue=1
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.UMBRAL_RACHA,
            'Días mínimos de valor idéntico consecutivo para reportar racha sospechosa',
            type=QgsProcessingParameterNumber.Type.Integer,
            defaultValue=7, minValue=2
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.Z_UMBRAL,
            'Umbral |z-score| (log-mensual) para reportar outliers',
            type=QgsProcessingParameterNumber.Type.Double,
            defaultValue=4.0, minValue=1.0
        ))
        self.addParameter(QgsProcessingParameterNumber(
            self.K_SIGMA_SALTO,
            'Umbral (media + k·sigma) de cambio dia a dia para reportar salto brusco',
            type=QgsProcessingParameterNumber.Type.Double,
            defaultValue=5.0, minValue=1.0
        ))
        self.addParameter(QgsProcessingParameterBoolean(
            self.REINDEXAR,
            'Completar fechas de calendario faltantes antes del relleno',
            defaultValue=True
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_CSV,
            'CSV corregido de salida (Date, Flow)',
            fileFilter='CSV (*.csv)'
        ))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_REPORTE,
            'Reporte de control de calidad (texto)',
            fileFilter='Texto (*.txt)'
        ))

    def processAlgorithm(self, parameters, context, feedback):
        input_path = self.parameterAsFile(parameters, self.INPUT, context)
        idx_formato_entrada = self.parameterAsEnum(parameters, self.FORMATO_ENTRADA, context)
        formato_entrada = self.FORMATOS_ENTRADA_KEY[idx_formato_entrada]
        col_fecha = self.parameterAsString(parameters, self.COL_FECHA, context).strip() or 'Date'
        col_flow = self.parameterAsString(parameters, self.COL_FLOW, context).strip() or 'Flow'
        idx_formato = self.parameterAsEnum(parameters, self.FORMATO_FECHA, context)
        formato_fecha = self.FORMATOS_FECHA_KEY[idx_formato]
        hoja_excel = self.parameterAsString(parameters, self.HOJA_EXCEL, context).strip() or None
        mes_inicio_hidro = self.parameterAsInt(parameters, self.MES_INICIO_ANIO_HIDRO, context)
        idx_metodo = self.parameterAsEnum(parameters, self.METODO, context)
        metodo = self.METODOS_KEY[idx_metodo]
        umbral_advertencia = self.parameterAsInt(parameters, self.UMBRAL_ADVERTENCIA, context)
        umbral_racha = self.parameterAsInt(parameters, self.UMBRAL_RACHA, context)
        z_umbral = self.parameterAsDouble(parameters, self.Z_UMBRAL, context)
        k_sigma_salto = self.parameterAsDouble(parameters, self.K_SIGMA_SALTO, context)
        reindexar = self.parameterAsBool(parameters, self.REINDEXAR, context)
        output_csv = self.parameterAsFileOutput(parameters, self.OUTPUT_CSV, context)
        output_reporte = self.parameterAsFileOutput(parameters, self.OUTPUT_REPORTE, context)

        if not os.path.isfile(input_path):
            raise QgsProcessingException(f"No se encontró el archivo: {input_path}")

        feedback.pushInfo('─' * 60)
        feedback.pushInfo(f"Leyendo: {input_path}")
        feedback.pushInfo(f"Formato de entrada: {self.FORMATOS_ENTRADA_LABEL[idx_formato_entrada]}")

        if formato_entrada == 'csv_largo':
            feedback.pushInfo(f"Columnas esperadas: {col_fecha}, {col_flow} "
                               f"| formato fecha: {formato_fecha}")
            try:
                registros, errores_fecha = qc.leer_csv(
                    input_path, col_fecha=col_fecha, col_flow=col_flow,
                    formato_fecha=formato_fecha
                )
            except ValueError as ex:
                raise QgsProcessingException(str(ex))

            if errores_fecha:
                feedback.pushWarning(
                    f"{len(errores_fecha)} filas con fecha no interpretable con el "
                    f"formato seleccionado — se ignoraron. Revisa el formato de fecha "
                    f"si esto es inesperado."
                )
                for num_fila, txt in errores_fecha[:10]:
                    feedback.pushInfo(f"  fila {num_fila}: '{txt}'")
        else:  # excel_ancho
            feedback.pushInfo(f"Hoja: {hoja_excel or '(primera hoja)'} "
                               f"| mes inicio año hidrológico: {mes_inicio_hidro}")
            try:
                registros, avisos = qc.leer_excel_ancho(
                    input_path, hoja=hoja_excel, mes_inicio_anio_hidro=mes_inicio_hidro
                )
            except ValueError as ex:
                raise QgsProcessingException(str(ex))

            if avisos:
                feedback.pushWarning(f"{len(avisos)} celdas/columnas omitidas al leer el Excel:")
                for a in avisos[:15]:
                    feedback.pushInfo(f"  {a}")
                if len(avisos) > 15:
                    feedback.pushInfo(f"  ... y {len(avisos) - 15} más (ver detalle completo en el log).")

        if not registros:
            raise QgsProcessingException(
                "No se pudo leer ningún registro válido del archivo de entrada. "
                "Revisa nombres de columnas / formato de fecha / estructura del Excel."
            )

        registros, duplicados = qc.deduplicar(registros)
        if duplicados:
            feedback.pushWarning(
                f"{len(duplicados)} fechas duplicadas — se conservó un solo "
                f"registro por fecha (prioridad al que tenga valor numérico)."
            )

        if reindexar:
            registros, insertadas = qc.reindexar_diario(registros)
            if insertadas:
                feedback.pushInfo(
                    f"Se insertaron {len(insertadas)} fechas de calendario "
                    f"que faltaban por completo en el archivo original."
                )

        feedback.pushInfo(f"Registros en la serie: {len(registros)}")
        feedback.pushInfo('─' * 60)
        feedback.pushInfo("Ejecutando diagnóstico de calidad...")

        diagnostico = qc.generar_diagnostico(
            registros, min_dias_racha=umbral_racha, z_umbral=z_umbral,
            k_sigma_salto=k_sigma_salto
        )

        feedback.pushInfo(f"Episodios de valores faltantes: "
                           f"{len(diagnostico['episodios_faltantes'])}")
        feedback.pushInfo(f"Valores negativos o cero: {len(diagnostico['negativos_cero'])}")
        feedback.pushInfo(f"Rachas de valor idéntico >= {umbral_racha} días: "
                           f"{len(diagnostico['rachas_planas'])} "
                           f"(reportadas, NO corregidas automáticamente)")
        feedback.pushInfo(f"Outliers estadísticos (|z|>{z_umbral}): "
                           f"{len(diagnostico['outliers'])} "
                           f"(reportados, NO corregidos automáticamente)")
        feedback.pushInfo(f"Saltos bruscos día a día (>media+{k_sigma_salto}·sigma): "
                           f"{len(diagnostico['saltos_bruscos'])} "
                           f"(reportados, NO corregidos automáticamente)")

        feedback.pushInfo('─' * 60)
        feedback.pushInfo(f"Rellenando vacíos — método: {metodo}")

        rellenados, episodios = qc.rellenar_vacios(
            registros, metodo=metodo, umbral_advertencia_dias=umbral_advertencia
        )

        for ep in episodios:
            estado = 'RELLENADO' if ep.get('rellenado') else 'NO RELLENADO'
            linea = (f"  {ep['inicio']} -> {ep['fin']} ({ep['dias']} días) "
                     f"[{estado}] método={ep.get('metodo_efectivo', metodo)}")
            if ep.get('advertencia'):
                feedback.pushWarning(linea + '  <-- GAP LARGO, revisar')
            else:
                feedback.pushInfo(linea)
            if ep.get('motivo_no_relleno'):
                feedback.pushWarning(f"      no rellenado: {ep['motivo_no_relleno']}")

        no_rellenados = [e for e in episodios if not e.get('rellenado')]
        if no_rellenados:
            feedback.pushWarning(
                f"{len(no_rellenados)} episodio(s) no se pudieron rellenar "
                f"(sin dato medido a algún lado de la serie)."
            )

        qc.escribir_csv(rellenados, output_csv, formato_fecha=formato_fecha,
                         col_fecha=col_fecha, col_flow=col_flow)
        qc.escribir_reporte_qc(diagnostico, episodios, output_reporte)

        feedback.pushInfo('─' * 60)
        feedback.pushInfo(f"CSV corregido: {output_csv}")
        feedback.pushInfo(f"Reporte QC: {output_reporte}")

        return {self.OUTPUT_CSV: output_csv, self.OUTPUT_REPORTE: output_reporte}

    def run(self):
        from qgis import processing
        processing.execAlgorithmDialog('geeswat:caudal_qc_relleno')
