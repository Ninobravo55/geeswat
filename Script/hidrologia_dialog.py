# -*- coding: utf-8 -*-
"""
hidrologia_dialog.py — geeSWAT
Herramienta independiente de Interpretación Hidrológica (Explorador de
proyecto QSWAT). No es una pestaña del diálogo principal: se abre como su
propia ventana desde el menú de geeSWAT, con su propia carpeta de salida.
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

# ── Paleta (coherente con geeswat_dialog.py) ─────────────────────────────────
AZUL     = '#0D47A1'
AZUL_M   = '#1565C0'
AZUL_C   = '#E3F2FD'
VERDE    = '#1B5E20'
VERDE_M  = '#2E7D32'
NARANJA  = '#E65100'
GRIS     = '#37474F'
GRIS_OS  = '#263238'


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


class HidrologiaHerramienta(QDialog):
    """Ventana independiente: Explorador de proyecto QSWAT / Interpretación
    Hidrológica. Calcula geometría, forma, relieve, red de drenaje, tiempo de
    concentración y curva hipsométrica, y guarda todo en una carpeta de salida
    propia (HTML + Markdown + CSV)."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle('geeSWAT — Interpretación Hidrológica')
        self.setMinimumSize(880, 640)
        self.setWindowFlag(Qt.Window, True)

        self._hid_informe_md = None
        self._hid_informe_html = None
        self._hid_carpeta_salida = None
        self._hid_nombre_cuenca = None
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
        inner = QWidget(); inner.setMinimumWidth(360)
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(14, 14, 8, 14); vl.setSpacing(14)

        lbl_titulo = QLabel('Explorador de proyecto QSWAT')
        lbl_titulo.setFont(QFont('Arial', 13, QFont.Bold))
        lbl_titulo.setStyleSheet(f'color:{AZUL};')
        vl.addWidget(lbl_titulo)

        lbl_intro = QLabel(
            'Calcula geometría, forma, relieve y red de drenaje de la cuenca '
            '(20+ índices morfométricos con su fórmula), tiempo de '
            'concentración y curva hipsométrica a partir de capas cargadas '
            'en QGIS. Guarda automáticamente todo en la carpeta de salida '
            'que elijas abajo.')
        lbl_intro.setWordWrap(True)
        lbl_intro.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl.addWidget(lbl_intro)

        # ── Capas requeridas ──
        g_capas = _grupo('Capas del proyecto QSWAT', AZUL)
        vl_c = QVBoxLayout(g_capas); vl_c.setSpacing(8)

        vl_c.addWidget(QLabel('Cuenca / Subcuencas (polígono):'))
        self._cmb_cuenca = QgsMapLayerComboBox()
        self._cmb_cuenca.setFilters(QgsMapLayerProxyModel.PolygonLayer)
        self._cmb_cuenca.setMinimumHeight(30)
        vl_c.addWidget(self._cmb_cuenca)

        vl_c.addWidget(QLabel('Red de drenaje (líneas — usa "Streams", la capa derivada del DEM):'))
        self._cmb_red = QgsMapLayerComboBox()
        self._cmb_red.setFilters(QgsMapLayerProxyModel.LineLayer)
        self._cmb_red.setMinimumHeight(30)
        self._cmb_red.layerChanged.connect(self._actualizar_campo_orden)
        vl_c.addWidget(self._cmb_red)

        lbl_orden = QLabel(
            'Campo de orden de Strahler (opcional — habilita longitud por '
            'orden, razón de bifurcación, textura de drenaje y sinuosidad). '
            'En proyectos QSWAT normalmente se llama "strmOrder" y está en '
            'la capa "Streams" (derivada del DEM) — la capa "Reaches" '
            '(canal enrutado, con campo Subbasin) no suele traerlo.')
        lbl_orden.setWordWrap(True)
        lbl_orden.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_c.addWidget(lbl_orden)
        self._cmb_orden = QgsFieldComboBox()
        self._cmb_orden.setAllowEmptyFieldName(True)
        self._cmb_orden.setMinimumHeight(30)
        self._cmb_orden.setLayer(self._cmb_red.currentLayer())
        vl_c.addWidget(self._cmb_orden)

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
        g_txt = _grupo('Carpeta TxtInOut de SWAT (opcional)', NARANJA)
        vl_t = QVBoxLayout(g_txt); vl_t.setSpacing(8)
        lbl_t = QLabel(
            'Si el modelo ya fue corrido con QSWAT, señala la carpeta '
            'TxtInOut para usar la longitud y pendiente de cauce (CH_L1, '
            'CH_S1) que el propio SWAT calculó por subcuenca.')
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

        # ── Carpeta de salida (propia de esta herramienta) ──
        g_out = _grupo('Carpeta de salida', VERDE)
        vl_o = QVBoxLayout(g_out); vl_o.setSpacing(8)
        lbl_o = QLabel('Todo el resultado (HTML + Markdown + CSV) se guarda aquí, '
                        'en una subcarpeta por cuenca y fecha/hora.')
        lbl_o.setWordWrap(True)
        lbl_o.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_o.addWidget(lbl_o)
        hl_o = QHBoxLayout()
        self._inp_salida = QLineEdit()
        self._inp_salida.setText(self._settings.value('geeswat/hidrologia_carpeta_salida', ''))
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
        self._btn_analizar = QPushButton('Analizar cuenca')
        self._btn_analizar.setStyleSheet(_btn_exito())
        self._btn_analizar.setMinimumHeight(42)
        self._btn_analizar.setFont(QFont('Arial', 11, QFont.Bold))
        self._btn_analizar.clicked.connect(self._ejecutar_analisis)
        vl.addWidget(self._btn_analizar)

        self._btn_abrir = QPushButton('Abrir interpretación en nueva ventana')
        self._btn_abrir.setStyleSheet(_btn_primario())
        self._btn_abrir.setMinimumHeight(38)
        self._btn_abrir.setEnabled(False)
        self._btn_abrir.clicked.connect(self._abrir_ventana_resultados)
        vl.addWidget(self._btn_abrir)

        self._btn_exportar = QPushButton('Exportar informe (.md)')
        self._btn_exportar.setStyleSheet(_btn_secundario())
        self._btn_exportar.setMinimumHeight(34)
        self._btn_exportar.setEnabled(False)
        self._btn_exportar.clicked.connect(self._exportar_informe_md)
        vl.addWidget(self._btn_exportar)

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
            'Selecciona las capas, la carpeta de salida, y presiona "Analizar cuenca"...')
        vl_d.addWidget(self._txt_log, 1)

        spl.addWidget(panel_der)
        spl.setSizes([440, 520])
        hl_root = QHBoxLayout(self)
        hl_root.setContentsMargins(0, 0, 0, 0)
        hl_root.addWidget(spl)

    # ──────────────────────────────────────────────────────────────────────
    def _actualizar_campo_orden(self, layer):
        self._cmb_orden.setLayer(layer)

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
            self._settings.setValue('geeswat/hidrologia_carpeta_salida', carpeta)

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

    def _log(self, msg):
        from qgis.PyQt.QtWidgets import QApplication
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._txt_log.append(f'[{ts}] {msg}')
        sb = self._txt_log.verticalScrollBar()
        sb.setValue(sb.maximum())
        QApplication.processEvents()

    # ──────────────────────────────────────────────────────────────────────
    def _ejecutar_analisis(self):
        from qgis.PyQt.QtWidgets import QApplication
        from .hidrologia_extraccion import (
            extraer_geometria_cuenca, extraer_red_drenaje, extraer_relieve_dem,
            leer_txtinout_subbasins, media_raster_zonal, analizar_red_por_orden,
        )
        from . import hidrologia_calculo as hc

        layer_cuenca = self._cmb_cuenca.currentLayer()
        layer_red    = self._cmb_red.currentLayer()
        layer_dem    = self._cmb_dem.currentLayer()
        layer_cn     = self._cmb_cn.currentLayer()

        if layer_cuenca is None:
            QMessageBox.warning(self, 'geeSWAT',
                'Selecciona la capa de cuenca/subcuencas (polígono).')
            return

        carpeta_base = self._inp_salida.text().strip()
        if not carpeta_base:
            QMessageBox.warning(self, 'geeSWAT',
                'Selecciona una carpeta de salida antes de analizar.')
            return

        self._txt_log.clear()
        log = self._log

        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            faltantes = []
            datos = {'nombre': layer_cuenca.name()}

            log('Calculando geometría de la cuenca...')
            geo = extraer_geometria_cuenca(layer_cuenca)
            if not geo:
                QMessageBox.warning(self, 'geeSWAT',
                    'No se pudo leer la geometría de la capa de cuenca.')
                return
            datos.update(geo)
            if geo.get('crs_geografica'):
                log('Aviso: la capa está en coordenadas geográficas (grados). '
                    'Se recalculó la longitud axial sobre el contorno, pero se '
                    'recomienda reproyectar a un CRS métrico (UTM).')

            kc = hc.indice_compacidad_gravelius(geo['area_km2'], geo['perimetro_km'])
            kf = re_ = None
            if geo.get('longitud_axial_km'):
                kf = hc.factor_forma_horton(geo['area_km2'], geo['longitud_axial_km'])
                re_ = hc.razon_elongacion_schumm(geo['area_km2'], geo['longitud_axial_km'])
            else:
                faltantes.append('No se pudo estimar la longitud axial de la cuenca '
                                  '(Kf y Re no calculados); Kc sí se calculó con área y perímetro.')
            if kc is not None:
                datos['forma'] = hc.interpretar_forma(kc, kf, re_)
            datos['kf'] = kf
            datos['re'] = re_
            datos['ancho_medio_km'] = hc.ancho_medio(geo['area_km2'], geo.get('longitud_axial_km'))
            datos['circularidad'] = hc.circularidad_miller(geo['area_km2'], geo['perimetro_km'])
            log(f'  Kc={kc}  Kf={kf}  Re={re_}  Circularidad={datos["circularidad"]}')

            log('Calculando densidad de drenaje...')
            dd = None
            if layer_red is not None:
                red = extraer_red_drenaje(layer_red)
                if red:
                    dd = hc.densidad_drenaje(red['long_total_km'], geo['area_km2'])
                    datos['dd_interp'] = hc.interpretar_densidad_drenaje(dd)
                    datos['_red'] = red
                    datos['frecuencia'] = hc.frecuencia_corrientes(red['n_tramos'], geo['area_km2'])
                    log(f"  Dd={dd} km/km2  (red: {red['n_tramos']} tramos, "
                        f"{red['long_total_km']} km totales)")
                else:
                    faltantes.append('La capa de red de drenaje no tiene features legibles; '
                                      'densidad de drenaje no calculada.')
            else:
                faltantes.append('No se seleccionó capa de red de drenaje; '
                                  'densidad de drenaje no calculada.')

            campo_orden = self._cmb_orden.currentField()
            if layer_red is not None and campo_orden:
                orden = analizar_red_por_orden(layer_red, campo_orden, log=log)
                if orden:
                    orden['rb_interp'] = hc.interpretar_bifurcacion(
                        hc.razon_bifurcacion_media(orden['conteo_por_orden']))
                    orden['dt_interp'] = hc.interpretar_textura(
                        hc.textura_drenaje(orden['n_tramos_total'], geo['perimetro_km']))
                    orden['sinuosidad'] = hc.sinuosidad(
                        orden['main_channel_length_km'], orden['main_channel_straight_km'])
                    datos['_orden'] = orden
                    log(f"  Orden máximo={orden['orden_max']}  "
                        f"Rb={orden['rb_interp']['valor'] if orden['rb_interp'] else '—'}  "
                        f"Sinuosidad={orden['sinuosidad']}")
                else:
                    faltantes.append(f'El campo de orden "{campo_orden}" no permitió agrupar '
                                      f'la red por orden de Strahler.')
            elif layer_red is not None:
                faltantes.append('No se indicó campo de orden de Strahler: longitud por orden, '
                                  'razón de bifurcación, textura de drenaje y sinuosidad no calculados.')

            log('Recortando DEM y calculando relieve / curva hipsométrica '
                '(puede tardar unos segundos)...')
            relieve = None
            if layer_dem is not None:
                relieve = extraer_relieve_dem(layer_dem, layer_cuenca, log=log)
                if relieve:
                    datos['relieve_total_m'] = relieve['relieve_total_m']
                    datos['e_min'] = relieve['e_min']
                    datos['e_max'] = relieve['e_max']
                    datos['e_media'] = relieve['e_media']
                    if relieve.get('pendiente_media_pct') is not None:
                        datos['relieve_interp'] = hc.interpretar_relieve_pendiente(
                            relieve['pendiente_media_pct'])
                    else:
                        faltantes.append('No se pudo calcular la pendiente media desde el DEM.')
                    curva = hc.curva_hipsometrica(relieve['pares_area_elevacion'])
                    datos['hipso_interp'] = hc.interpretar_hipsometria(curva)
                    datos['_curva'] = curva
                    if dd is not None:
                        datos['rugosidad'] = hc.indice_rugosidad(dd, relieve['relieve_total_m'])
                    datos['melton_interp'] = hc.interpretar_melton(
                        hc.indice_melton(relieve['relieve_total_m'], geo['area_km2']))
                    log(f"  Emin={relieve['e_min']} m  Emax={relieve['e_max']} m  "
                        f"Pendiente media={relieve.get('pendiente_media_pct')}%  "
                        f"Melton={datos['melton_interp']['valor'] if datos['melton_interp'] else '—'}")
                else:
                    faltantes.append('No se pudo procesar el DEM para esta cuenca '
                                      '(revisa que cubra el área y que GDAL/numpy '
                                      'estén disponibles en el Python de QGIS).')
            else:
                faltantes.append('No se seleccionó capa DEM; relieve y curva '
                                  'hipsométrica no calculados.')

            log('Calculando tiempo de concentración...')
            carpeta_txt = self._inp_txtinout.text().strip()
            subs_txt = leer_txtinout_subbasins(carpeta_txt, log=log) if carpeta_txt else []
            tc = {}
            if subs_txt:
                sub_principal = max(subs_txt, key=lambda s: s.get('ch_l1_km', 0))
                cn_val = None
                if layer_cn is not None:
                    cn_val = media_raster_zonal(layer_cn, layer_cuenca, log=log)
                desnivel = relieve['relieve_total_m'] if relieve else None
                tc = hc.resumen_tc(
                    sub_principal.get('ch_l1_km', 0),
                    sub_principal.get('ch_s1_mm', 0),
                    desnivel or 0,
                    cn=cn_val,
                    pendiente_pct=(sub_principal.get('ch_s1_mm', 0) * 100) if sub_principal.get('ch_s1_mm') else None,
                )
                log(f"  Usando cauce de subcuenca {sub_principal.get('subbasin')} "
                    f"(CH_L1={sub_principal.get('ch_l1_km')} km, "
                    f"CH_S1={sub_principal.get('ch_s1_mm')} m/m) como aproximación "
                    f"del cauce principal.")
            elif layer_red is not None and relieve:
                red = datos.get('_red')
                tc = hc.resumen_tc(
                    red['cauce_mas_largo_km'],
                    relieve['relieve_total_m'] / (red['cauce_mas_largo_km'] * 1000) if red['cauce_mas_largo_km'] else 0,
                    relieve['relieve_total_m'])
                log('  No se indicó carpeta TxtInOut: se aproximó el cauce principal '
                    'con el tramo más largo de la red y la pendiente promedio del '
                    'relieve total. Aproximación burda — para un resultado confiable, '
                    'indica la carpeta TxtInOut.')
            else:
                faltantes.append('No hay datos suficientes (ni TxtInOut ni red+DEM) '
                                  'para calcular tiempo de concentración.')
            if tc:
                datos['tc'] = tc

            informe_md = hc.construir_informe(datos, faltantes)
            informe_html = hc.construir_informe_html(datos, faltantes, logo_base64=self._logo_base64())
            tabla_csv = hc.construir_tabla_csv(datos)
            self._hid_informe_md = informe_md
            self._hid_informe_html = informe_html
            self._hid_nombre_cuenca = datos.get('nombre', 'cuenca')

            seguro = re.sub(r'[^\w\-]+', '_', self._hid_nombre_cuenca)
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
            carpeta_salida = os.path.join(carpeta_base, f'{seguro}_{ts}')
            try:
                os.makedirs(carpeta_salida, exist_ok=True)
                with open(os.path.join(carpeta_salida, 'informe_hidrologico.html'), 'w', encoding='utf-8') as fh:
                    fh.write(informe_html)
                with open(os.path.join(carpeta_salida, 'informe_hidrologico.md'), 'w', encoding='utf-8') as fh:
                    fh.write(informe_md)
                with open(os.path.join(carpeta_salida, 'parametros_morfometricos.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
                    fh.write(tabla_csv)
                self._hid_carpeta_salida = carpeta_salida
                log(f'Guardado en: {carpeta_salida}')
            except Exception as e:
                log(f'No se pudo guardar en la carpeta de salida: {e}')
                self._hid_carpeta_salida = None

            self._btn_exportar.setEnabled(True)
            self._btn_abrir.setEnabled(True)
            log('Análisis completo. Usa "Abrir interpretación en nueva ventana" para verlo.')

        except Exception as ex:
            import traceback
            QMessageBox.critical(self, 'geeSWAT — Error', f'Error durante el análisis:\n{ex}')
            log(traceback.format_exc())
        finally:
            QApplication.restoreOverrideCursor()

    def _abrir_ventana_resultados(self):
        if not self._hid_informe_html:
            QMessageBox.information(self, 'geeSWAT', 'Primero presiona "Analizar cuenca".')
            return
        from .hidrologia_reporte import ResultadosHidrologiaDialog
        carpeta_sugerida = self._hid_carpeta_salida or self._inp_salida.text().strip()
        self._resultados_win = ResultadosHidrologiaDialog(
            self._hid_informe_html, self._hid_nombre_cuenca,
            carpeta_sugerida=carpeta_sugerida, parent=self)
        self._resultados_win.show()
        self._resultados_win.raise_()
        self._resultados_win.activateWindow()

    def _exportar_informe_md(self):
        if not self._hid_informe_md:
            return
        ruta, _ = QFileDialog.getSaveFileName(
            self, 'Exportar informe (texto)', 'informe_hidrologico.md',
            'Markdown (*.md);;Texto (*.txt)')
        if not ruta:
            return
        try:
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write(self._hid_informe_md)
            QMessageBox.information(self, 'geeSWAT', f'Informe exportado a:\n{ruta}')
        except Exception as ex:
            QMessageBox.critical(self, 'geeSWAT — Error', f'No se pudo guardar el informe:\n{ex}')
