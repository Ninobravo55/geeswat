# -*- coding: utf-8 -*-
"""
hidrologia_subcuencas_dialog.py — geeSWAT
Herramienta independiente: analiza TODAS las subcuencas de una capa, una por
una, reutilizando el mismo motor de cálculo/extracción que la herramienta de
cuenca única (hidrologia_calculo.py / hidrologia_extraccion.py). Genera un
informe HTML individual por subcuenca (subcarpeta por subcuenca) más un
resumen comparativo (HTML + CSV ancho) con todas juntas.
"""

import os
import re
import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QWidget, QLabel, QLineEdit,
    QPushButton, QFileDialog, QMessageBox, QGroupBox, QScrollArea, QFrame,
    QSplitter, QTextEdit
)
from qgis.PyQt.QtCore import Qt, QSettings
from qgis.PyQt.QtGui import QFont

from qgis.core import QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox

AZUL, AZUL_M, VERDE, VERDE_M, NARANJA, GRIS, GRIS_OS = (
    '#0D47A1', '#1565C0', '#1B5E20', '#2E7D32', '#E65100', '#37474F', '#263238')


def _btn_primario(color=AZUL_M, hover=AZUL):
    return (f'QPushButton {{background:{color};color:white;border:none;'
            f'border-radius:6px;padding:10px 18px;font-weight:bold;font-size:12px;}}'
            f'QPushButton:hover {{background:{hover};}}'
            f'QPushButton:disabled {{background:#90A4AE;color:#CFD8DC;}}')


def _btn_secundario():
    return (f'QPushButton {{background:white;color:{GRIS};border:1.5px solid #B0BEC5;'
            f'border-radius:6px;padding:8px 15px;font-size:11px;}}'
            f'QPushButton:hover {{background:#ECEFF1;}}'
            f'QPushButton:disabled {{color:#B0BEC5;}}')


def _btn_exito():
    return _btn_primario(VERDE_M, VERDE)


def _grupo(titulo, color=AZUL):
    g = QGroupBox(titulo)
    g.setStyleSheet(
        f'QGroupBox {{font-weight:bold;color:{color};border:1.5px solid #CFD8DC;'
        f'border-radius:8px;margin-top:10px;padding:12px;}}'
        f'QGroupBox::title {{subcontrol-origin:margin;left:12px;padding:0 5px;}}')
    return g


class HidrologiaSubcuencasHerramienta(QDialog):
    """Ventana independiente: analiza morfométricamente TODAS las subcuencas
    de una capa, una por una, y genera informes individuales + un resumen
    comparativo."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('geeSWAT — Interpretación Hidrológica de Todas las Subcuencas')
        self.setMinimumSize(900, 660)
        self.setWindowFlag(Qt.Window, True)

        self._resumen_html = None
        self._carpeta_salida_final = None
        self._resultados_win = None
        self._settings = QSettings()

        self._construir_ui()

    # ──────────────────────────────────────────────────────────────────────
    def _construir_ui(self):
        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(6)
        spl.setStyleSheet('QSplitter::handle {background:#CFD8DC;}')

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setMinimumWidth(370)
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(14, 14, 8, 14); vl.setSpacing(14)

        lbl_titulo = QLabel('Interpretación Hidrológica — Todas las Subcuencas')
        lbl_titulo.setFont(QFont('Arial', 12, QFont.Bold))
        lbl_titulo.setStyleSheet(f'color:{AZUL};')
        lbl_titulo.setWordWrap(True)
        vl.addWidget(lbl_titulo)

        lbl_intro = QLabel(
            'Calcula los mismos índices morfométricos que la herramienta de '
            'cuenca única, pero subcuenca por subcuenca, y genera un informe '
            'HTML por cada una más una tabla comparativa consolidada.')
        lbl_intro.setWordWrap(True)
        lbl_intro.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl.addWidget(lbl_intro)

        # ── Capas requeridas ──
        g_capas = _grupo('Capas del proyecto QSWAT', AZUL)
        vl_c = QVBoxLayout(g_capas); vl_c.setSpacing(8)

        vl_c.addWidget(QLabel('Subcuencas (polígono, una feature por subcuenca):'))
        self._cmb_cuencas = QgsMapLayerComboBox()
        self._cmb_cuencas.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self._cmb_cuencas.setMinimumHeight(30)
        self._cmb_cuencas.layerChanged.connect(self._actualizar_campos_cuenca)
        vl_c.addWidget(self._cmb_cuencas)

        vl_c.addWidget(QLabel('Campo identificador de subcuenca (p.ej. "Subbasin"):'))
        self._cmb_campo_id = QgsFieldComboBox()
        self._cmb_campo_id.setMinimumHeight(30)
        self._cmb_campo_id.setLayer(self._cmb_cuencas.currentLayer())
        vl_c.addWidget(self._cmb_campo_id)

        lbl_red = QLabel('Red de drenaje completa — capa "Streams" (la que trae strmOrder):')
        lbl_red.setWordWrap(True)
        vl_c.addWidget(lbl_red)
        self._cmb_red = QgsMapLayerComboBox()
        self._cmb_red.setFilters(QgsMapLayerProxyModel.LineLayer)
        self._cmb_red.setMinimumHeight(30)
        self._cmb_red.layerChanged.connect(self._actualizar_campos_red)
        vl_c.addWidget(self._cmb_red)

        lbl_streams_nota = QLabel(
            'Se usa para densidad de drenaje, frecuencia y (si indicas el '
            'campo de orden) bifurcación/textura/sinuosidad. Se filtra por '
            'subcuenca por intersección espacial (esta capa normalmente no '
            'trae campo Subbasin).')
        lbl_streams_nota.setWordWrap(True)
        lbl_streams_nota.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_c.addWidget(lbl_streams_nota)

        lbl_orden = QLabel('Campo de orden de Strahler (p.ej. "strmOrder"):')
        lbl_orden.setWordWrap(True)
        lbl_orden.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_c.addWidget(lbl_orden)
        self._cmb_orden = QgsFieldComboBox()
        self._cmb_orden.setAllowEmptyFieldName(True)
        self._cmb_orden.setMinimumHeight(30)
        self._cmb_orden.setLayer(self._cmb_red.currentLayer())
        vl_c.addWidget(self._cmb_orden)

        lbl_reaches = QLabel(
            'Reaches / canal enrutado — capa "Reaches" con campo Subbasin '
            '(opcional, recomendado): un tramo por subcuenca, se usa para el '
            'canal principal en tiempo de concentración cuando no tienes '
            'la carpeta TxtInOut — más preciso que aproximar con la red completa.')
        lbl_reaches.setWordWrap(True)
        lbl_reaches.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_c.addWidget(lbl_reaches)
        self._cmb_reaches = QgsMapLayerComboBox()
        self._cmb_reaches.setFilters(QgsMapLayerProxyModel.LineLayer)
        self._cmb_reaches.setAllowEmptyLayer(True)
        self._cmb_reaches.setCurrentIndex(0)
        self._cmb_reaches.setMinimumHeight(30)
        self._cmb_reaches.layerChanged.connect(self._actualizar_campo_reaches)
        vl_c.addWidget(self._cmb_reaches)

        vl_c.addWidget(QLabel('Campo Subbasin en Reaches:'))
        self._cmb_campo_reaches = QgsFieldComboBox()
        self._cmb_campo_reaches.setAllowEmptyFieldName(True)
        self._cmb_campo_reaches.setMinimumHeight(30)
        self._cmb_campo_reaches.setLayer(self._cmb_reaches.currentLayer())
        vl_c.addWidget(self._cmb_campo_reaches)

        vl_c.addWidget(QLabel('DEM (ráster de elevación):'))
        self._cmb_dem = QgsMapLayerComboBox()
        self._cmb_dem.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self._cmb_dem.setMinimumHeight(30)
        vl_c.addWidget(self._cmb_dem)

        lbl_cn = QLabel('Curva Número (ráster, opcional — habilita método SCS Lag):')
        lbl_cn.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_c.addWidget(lbl_cn)
        self._cmb_cn = QgsMapLayerComboBox()
        self._cmb_cn.setFilters(QgsMapLayerProxyModel.RasterLayer)
        self._cmb_cn.setAllowEmptyLayer(True)
        self._cmb_cn.setCurrentIndex(0)
        self._cmb_cn.setMinimumHeight(30)
        vl_c.addWidget(self._cmb_cn)

        vl.addWidget(g_capas)

        # ── TxtInOut opcional ──
        g_txt = _grupo('Carpeta TxtInOut de SWAT (opcional, recomendada)', NARANJA)
        vl_t = QVBoxLayout(g_txt); vl_t.setSpacing(8)
        lbl_t = QLabel(
            'Si el modelo ya fue corrido con QSWAT, se usa CH_L1/CH_S1 de cada '
            'subcuenca (un archivo .sub por subcuenca) para el tiempo de '
            'concentración — mismo criterio para las 11 subcuencas.')
        lbl_t.setWordWrap(True)
        lbl_t.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_t.addWidget(lbl_t)
        hl_t = QHBoxLayout()
        self._inp_txtinout = QLineEdit()
        self._inp_txtinout.setPlaceholderText('.../Scenarios/Default/TxtInOut')
        self._inp_txtinout.setMinimumHeight(30)
        btn_txt = QPushButton('Explorar…')
        btn_txt.setStyleSheet(_btn_secundario())
        btn_txt.setMinimumHeight(30)
        btn_txt.clicked.connect(self._elegir_txtinout)
        hl_t.addWidget(self._inp_txtinout, 1)
        hl_t.addWidget(btn_txt)
        vl_t.addLayout(hl_t)
        vl.addWidget(g_txt)

        # ── Carpeta de salida ──
        g_out = _grupo('Carpeta de salida', VERDE)
        vl_o = QVBoxLayout(g_out); vl_o.setSpacing(8)
        lbl_o = QLabel('Se crea una subcarpeta por corrida, con un informe HTML '
                        'por cada subcuenca más el resumen comparativo (HTML + CSV).')
        lbl_o.setWordWrap(True)
        lbl_o.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_o.addWidget(lbl_o)
        hl_o = QHBoxLayout()
        self._inp_salida = QLineEdit()
        self._inp_salida.setText(self._settings.value('geeswat/hidrologia_subcuencas_carpeta_salida', ''))
        self._inp_salida.setPlaceholderText('Selecciona la carpeta de salida...')
        self._inp_salida.setMinimumHeight(30)
        btn_out = QPushButton('Explorar…')
        btn_out.setStyleSheet(_btn_secundario())
        btn_out.setMinimumHeight(30)
        btn_out.clicked.connect(self._elegir_carpeta_salida)
        hl_o.addWidget(self._inp_salida, 1)
        hl_o.addWidget(btn_out)
        vl_o.addLayout(hl_o)
        vl.addWidget(g_out)

        # ── Acciones ──
        self._btn_analizar = QPushButton('Analizar todas las subcuencas')
        self._btn_analizar.setStyleSheet(_btn_exito())
        self._btn_analizar.setMinimumHeight(42)
        self._btn_analizar.setFont(QFont('Arial', 11, QFont.Bold))
        self._btn_analizar.clicked.connect(self._ejecutar_analisis)
        vl.addWidget(self._btn_analizar)

        self._btn_abrir = QPushButton('Abrir resumen en nueva ventana')
        self._btn_abrir.setStyleSheet(_btn_primario())
        self._btn_abrir.setMinimumHeight(38)
        self._btn_abrir.setEnabled(False)
        self._btn_abrir.clicked.connect(self._abrir_ventana_resultados)
        vl.addWidget(self._btn_abrir)

        vl.addStretch()
        scroll.setWidget(inner)
        spl.addWidget(scroll)

        # ── Panel derecho: log ──
        panel_der = QWidget(); panel_der.setMinimumWidth(380)
        vl_d = QVBoxLayout(panel_der)
        vl_d.setContentsMargins(8, 14, 14, 14); vl_d.setSpacing(8)
        lbl_h = QLabel('Registro del análisis')
        lbl_h.setStyleSheet(
            f'background:{GRIS_OS};color:white;padding:9px 14px;'
            f'font-weight:bold;font-size:12px;border-radius:6px 6px 0 0;')
        vl_d.addWidget(lbl_h)
        self._txt_log = QTextEdit()
        self._txt_log.setReadOnly(True)
        self._txt_log.setStyleSheet(
            'QTextEdit {background:#1E272E;color:#ECF0F1;'
            'font-family:Consolas,monospace;font-size:10px;'
            'border:1.5px solid #CFD8DC;border-top:none;'
            'border-radius:0 0 6px 6px;padding:8px;}')
        self._txt_log.setPlaceholderText(
            'Selecciona las capas, el campo ID de subcuenca y la carpeta de '
            'salida, y presiona "Analizar todas las subcuencas"...')
        vl_d.addWidget(self._txt_log, 1)

        spl.addWidget(panel_der)
        spl.setSizes([450, 520])
        hl_root = QHBoxLayout(self)
        hl_root.setContentsMargins(0, 0, 0, 0)
        hl_root.addWidget(spl)

    # ──────────────────────────────────────────────────────────────────────
    def _actualizar_campos_cuenca(self, layer):
        self._cmb_campo_id.setLayer(layer)

    def _actualizar_campos_red(self, layer):
        self._cmb_orden.setLayer(layer)

    def _actualizar_campo_reaches(self, layer):
        self._cmb_campo_reaches.setLayer(layer)

    def _elegir_txtinout(self):
        carpeta = QFileDialog.getExistingDirectory(
            self, 'Selecciona la carpeta TxtInOut', self._inp_txtinout.text().strip())
        if carpeta:
            self._inp_txtinout.setText(carpeta)

    def _elegir_carpeta_salida(self):
        carpeta = QFileDialog.getExistingDirectory(
            self, 'Selecciona la carpeta de salida', self._inp_salida.text().strip())
        if carpeta:
            self._inp_salida.setText(carpeta)
            self._settings.setValue('geeswat/hidrologia_subcuencas_carpeta_salida', carpeta)

    def _log(self, msg):
        from qgis.PyQt.QtWidgets import QApplication
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._txt_log.append(f'[{ts}] {msg}')
        sb = self._txt_log.verticalScrollBar()
        sb.setValue(sb.maximum())
        QApplication.processEvents()

    def _logo_base64(self):
        if hasattr(self, '_logo_b64_cache'):
            return self._logo_b64_cache
        import base64
        ruta = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             'Icons', 'logo_geomatica.jpg')
        try:
            with open(ruta, 'rb') as f:
                self._logo_b64_cache = base64.b64encode(f.read()).decode('ascii')
        except Exception:
            self._logo_b64_cache = None
        return self._logo_b64_cache

    # ──────────────────────────────────────────────────────────────────────
    def _analizar_una_subcuenca(self, hx, hc, feature, nombre, layer_cuencas, layer_red,
                                 layer_dem, layer_cn, valor_id, campo_orden,
                                 layer_reaches, campo_reaches, subs_txt, log):
        """Reproduce, para UNA subcuenca, el mismo pipeline que la herramienta
        de cuenca única — devuelve (datos, faltantes)."""
        faltantes = []
        datos = {'nombre': nombre}

        layer_sub = hx.layer_de_feature(layer_cuencas, feature)
        geo = hx.extraer_geometria_cuenca(layer_sub)
        if not geo:
            faltantes.append('No se pudo leer la geometría de esta subcuenca.')
            return datos, faltantes
        datos.update(geo)

        kc = hc.indice_compacidad_gravelius(geo['area_km2'], geo['perimetro_km'])
        kf = re_ = None
        if geo.get('longitud_axial_km'):
            kf = hc.factor_forma_horton(geo['area_km2'], geo['longitud_axial_km'])
            re_ = hc.razon_elongacion_schumm(geo['area_km2'], geo['longitud_axial_km'])
        else:
            faltantes.append('No se pudo estimar la longitud axial (Kf y Re no calculados).')
        if kc is not None:
            datos['forma'] = hc.interpretar_forma(kc, kf, re_)
        datos['kf'] = kf
        datos['re'] = re_
        datos['ancho_medio_km'] = hc.ancho_medio(geo['area_km2'], geo.get('longitud_axial_km'))
        datos['circularidad'] = hc.circularidad_miller(geo['area_km2'], geo['perimetro_km'])

        dd = None
        red_sub = None
        if layer_red is not None:
            red_sub = hx.filtrar_red_por_subcuenca(
                layer_red, feature.geometry(), campo_vinculo=None, valor_id=None)
            red = hx.extraer_red_drenaje(red_sub)
            if red:
                dd = hc.densidad_drenaje(red['long_total_km'], geo['area_km2'])
                datos['dd_interp'] = hc.interpretar_densidad_drenaje(dd)
                datos['_red'] = red
                datos['frecuencia'] = hc.frecuencia_corrientes(red['n_tramos'], geo['area_km2'])
            else:
                faltantes.append('Sin tramos de red asociados a esta subcuenca.')
        else:
            faltantes.append('No se seleccionó capa de red de drenaje.')

        if red_sub is not None and campo_orden:
            orden = hx.analizar_red_por_orden(red_sub, campo_orden, log=log)
            if orden:
                orden['rb_interp'] = hc.interpretar_bifurcacion(
                    hc.razon_bifurcacion_media(orden['conteo_por_orden']))
                orden['dt_interp'] = hc.interpretar_textura(
                    hc.textura_drenaje(orden['n_tramos_total'], geo['perimetro_km']))
                orden['sinuosidad'] = hc.sinuosidad(
                    orden['main_channel_length_km'], orden['main_channel_straight_km'])
                datos['_orden'] = orden

        relieve = None
        if layer_dem is not None:
            relieve = hx.extraer_relieve_dem(layer_dem, layer_sub, log=log)
            if relieve:
                datos['relieve_total_m'] = relieve['relieve_total_m']
                datos['e_min'] = relieve['e_min']
                datos['e_max'] = relieve['e_max']
                datos['e_media'] = relieve['e_media']
                if relieve.get('pendiente_media_pct') is not None:
                    datos['relieve_interp'] = hc.interpretar_relieve_pendiente(
                        relieve['pendiente_media_pct'])
                curva = hc.curva_hipsometrica(relieve['pares_area_elevacion'])
                datos['hipso_interp'] = hc.interpretar_hipsometria(curva)
                datos['_curva'] = curva
                if dd is not None:
                    datos['rugosidad'] = hc.indice_rugosidad(dd, relieve['relieve_total_m'])
                datos['melton_interp'] = hc.interpretar_melton(
                    hc.indice_melton(relieve['relieve_total_m'], geo['area_km2']))
            else:
                faltantes.append('No se pudo procesar el DEM para esta subcuenca.')
        else:
            faltantes.append('No se seleccionó capa DEM.')

        sub_txt = None
        if subs_txt:
            for s in subs_txt:
                if s.get('subbasin') == valor_id:
                    sub_txt = s
                    break

        reach_km = None
        if layer_reaches is not None and campo_reaches:
            reach_sub = hx.filtrar_red_por_subcuenca(
                layer_reaches, feature.geometry(),
                campo_vinculo=campo_reaches, valor_id=valor_id)
            reach_info = hx.extraer_red_drenaje(reach_sub)
            if reach_info:
                reach_km = reach_info['long_total_km']

        tc = {}
        cn_val = None
        if layer_cn is not None:
            cn_val = hx.media_raster_zonal(layer_cn, layer_sub, log=log)

        if sub_txt:
            desnivel = relieve['relieve_total_m'] if relieve else None
            tc = hc.resumen_tc(
                sub_txt.get('ch_l1_km', 0), sub_txt.get('ch_s1_mm', 0), desnivel or 0,
                cn=cn_val,
                pendiente_pct=(sub_txt.get('ch_s1_mm', 0) * 100) if sub_txt.get('ch_s1_mm') else None)
        elif reach_km and relieve:
            pendiente_mm = relieve['relieve_total_m'] / (reach_km * 1000)
            tc = hc.resumen_tc(reach_km, pendiente_mm, relieve['relieve_total_m'],
                                cn=cn_val, pendiente_pct=pendiente_mm * 100)
            log(f'  Subcuenca {valor_id}: usando longitud de Reaches ({reach_km} km) '
                f'con pendiente aproximada desde el relieve total (sin TxtInOut).')
        elif red_sub is not None and relieve:
            red = datos.get('_red')
            if red and red.get('cauce_mas_largo_km'):
                tc = hc.resumen_tc(
                    red['cauce_mas_largo_km'],
                    relieve['relieve_total_m'] / (red['cauce_mas_largo_km'] * 1000),
                    relieve['relieve_total_m'])
            else:
                faltantes.append('Sin datos suficientes para tiempo de concentración.')
        else:
            faltantes.append('Sin datos suficientes para tiempo de concentración.')
        if tc:
            datos['tc'] = tc

        return datos, faltantes

    def _ejecutar_analisis(self):
        from qgis.PyQt.QtWidgets import QApplication
        from . import hidrologia_extraccion as hx
        from . import hidrologia_calculo as hc

        layer_cuencas = self._cmb_cuencas.currentLayer()
        layer_red = self._cmb_red.currentLayer()
        layer_dem = self._cmb_dem.currentLayer()
        layer_cn = self._cmb_cn.currentLayer()
        layer_reaches = self._cmb_reaches.currentLayer()
        campo_reaches = self._cmb_campo_reaches.currentField() or None
        campo_id = self._cmb_campo_id.currentField()
        campo_orden = self._cmb_orden.currentField() or None
        carpeta_base = self._inp_salida.text().strip()

        if layer_cuencas is None or not campo_id:
            QMessageBox.warning(self, 'geeSWAT',
                'Selecciona la capa de subcuencas y su campo identificador.')
            return
        if not carpeta_base:
            QMessageBox.warning(self, 'geeSWAT',
                'Selecciona una carpeta de salida antes de analizar.')
            return

        self._txt_log.clear()
        log = self._log
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            carpeta_txt = self._inp_txtinout.text().strip()
            subs_txt = hx.leer_txtinout_subbasins(carpeta_txt, log=log) if carpeta_txt else []

            features = list(layer_cuencas.getFeatures())
            n = len(features)
            log(f'{n} subcuencas encontradas en la capa. Iniciando análisis...')

            lista_datos, lista_faltantes = [], []
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
            carpeta_corrida = os.path.join(carpeta_base, f'Subcuencas_{ts}')
            os.makedirs(carpeta_corrida, exist_ok=True)

            for i, feat in enumerate(features, start=1):
                valor_id = feat[campo_id]
                nombre = f'{layer_cuencas.name()} — {campo_id}={valor_id}'
                log(f'[{i}/{n}] Analizando subcuenca {valor_id}...')
                try:
                    datos, faltantes = self._analizar_una_subcuenca(
                        hx, hc, feat, nombre, layer_cuencas, layer_red, layer_dem,
                        layer_cn, valor_id, campo_orden, layer_reaches, campo_reaches,
                        subs_txt, log)
                except Exception as e:
                    import traceback
                    log(f'  ERROR en subcuenca {valor_id}: {e}')
                    log(traceback.format_exc())
                    datos, faltantes = {'nombre': nombre}, [f'Error durante el análisis: {e}']

                lista_datos.append(datos)
                lista_faltantes.append(faltantes)

                # Informe individual
                try:
                    seguro = re.sub(r'[^\w\-]+', '_', str(valor_id))
                    carpeta_sub = os.path.join(carpeta_corrida, f'subcuenca_{seguro}')
                    os.makedirs(carpeta_sub, exist_ok=True)
                    html_ind = hc.construir_informe_html(datos, faltantes, logo_base64=self._logo_base64())
                    with open(os.path.join(carpeta_sub, 'informe_hidrologico.html'), 'w', encoding='utf-8') as fh:
                        fh.write(html_ind)
                    with open(os.path.join(carpeta_sub, 'parametros_morfometricos.csv'), 'w',
                              encoding='utf-8-sig', newline='') as fh:
                        fh.write(hc.construir_tabla_csv(datos))
                except Exception as e:
                    log(f'  No se pudo guardar el informe individual de {valor_id}: {e}')

            log('Generando resumen comparativo...')
            resumen_html = hc.construir_resumen_multicuencas_html(
                lista_datos, lista_faltantes, logo_base64=self._logo_base64())
            resumen_csv = hc.construir_tabla_csv_multicuencas(lista_datos)

            with open(os.path.join(carpeta_corrida, 'resumen_subcuencas.html'), 'w', encoding='utf-8') as fh:
                fh.write(resumen_html)
            with open(os.path.join(carpeta_corrida, 'resumen_subcuencas.csv'), 'w',
                      encoding='utf-8-sig', newline='') as fh:
                fh.write(resumen_csv)

            self._resumen_html = resumen_html
            self._carpeta_salida_final = carpeta_corrida
            self._btn_abrir.setEnabled(True)
            log(f'Análisis completo. Guardado en: {carpeta_corrida}')
            log('Usa "Abrir resumen en nueva ventana" para ver la comparación.')

        except Exception as ex:
            import traceback
            QMessageBox.critical(self, 'geeSWAT — Error', f'Error durante el análisis:\n{ex}')
            log(traceback.format_exc())
        finally:
            QApplication.restoreOverrideCursor()

    def _abrir_ventana_resultados(self):
        if not self._resumen_html:
            QMessageBox.information(self, 'geeSWAT', 'Primero presiona "Analizar todas las subcuencas".')
            return
        from .hidrologia_reporte import ResultadosHidrologiaDialog
        self._resultados_win = ResultadosHidrologiaDialog(
            self._resumen_html, 'Todas las subcuencas',
            carpeta_sugerida=self._carpeta_salida_final or self._inp_salida.text().strip(),
            parent=self)
        self._resultados_win.show()
        self._resultados_win.raise_()
        self._resultados_win.activateWindow()
