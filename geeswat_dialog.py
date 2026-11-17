# -*- coding: utf-8 -*-
"""
geeswat_dialog.py — geeSWAT v1.2.3
Diálogo principal: Config GEE · Seleccionar AOI · MDE
La descarga del MDE corre en un QThread para no bloquear QGIS.
"""
import os
import math
import time
import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget,
    QLabel, QLineEdit, QPushButton, QProgressBar,
    QFileDialog, QMessageBox,
    QGroupBox, QGridLayout, QScrollArea, QFrame,
    QComboBox, QSpinBox, QSplitter, QSizePolicy, QTextEdit, QDateEdit, QCheckBox
)
from qgis.PyQt.QtCore import Qt, QSettings, QTimer, QUrl, QThread, pyqtSignal, QDate
from qgis.PyQt.QtGui import (
    QFont, QDesktopServices, QPixmap, QColor,
    QPainter, QBrush, QPen, QPainterPath, QPolygon
)
from qgis.PyQt.QtCore import QPoint

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsRasterLayer,
    QgsGeometry, QgsPointXY,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsDistanceArea, QgsWkbTypes, QgsMapLayerProxyModel
)
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand, QgsMapLayerComboBox

try:
    import ee
    EE_DISPONIBLE = True
except ImportError:
    EE_DISPONIBLE = False

# ── Paleta geeSWAT ────────────────────────────────────────────────────────────
AZUL     = '#0D47A1'
AZUL_M   = '#1565C0'
AZUL_C   = '#E3F2FD'
AZUL_OS  = '#0A2F6E'
VERDE    = '#1B5E20'
VERDE_M  = '#2E7D32'
VERDE_C  = '#E8F5E9'
NARANJA  = '#E65100'
NARANJA_C= '#FFF3E0'
ROJO     = '#C62828'
ROJO_C   = '#FFEBEE'
GRIS     = '#37474F'
GRIS_C   = '#ECEFF1'
GRIS_OS  = '#263238'
CYAN     = '#006064'
CYAN_C   = '#E0F7FA'

# ── Catálogo DEMs (igual que el algoritmo Processing) ────────────────────────
from .Script.mde_descargar_mde import GEE_DEMS, DEM_LABELS
LIMITE_MP  = 10_000_000
GDRIVE_FOLDER = 'GEE_geeSWAT'
DTYPE_BYTES   = {'Int16': 2, 'Int32': 4, 'Float32': 4, 'Float64': 8, 'Byte': 1}


# ── Estilos ───────────────────────────────────────────────────────────────────
def _btn_primario(color=AZUL_M, hover=AZUL):
    return (f'QPushButton {{background:{color};color:white;border:none;'
            f'border-radius:6px;padding:9px 18px;font-weight:bold;font-size:12px;}}'
            f'QPushButton:hover {{background:{hover};}}'
            f'QPushButton:disabled {{background:#90A4AE;color:#CFD8DC;}}')

def _btn_secundario():
    return (f'QPushButton {{background:white;color:{GRIS};border:1.5px solid #B0BEC5;'
            f'border-radius:6px;padding:7px 15px;font-size:11px;}}'
            f'QPushButton:hover {{background:{GRIS_C};}}'
            f'QPushButton:disabled {{color:#B0BEC5;}}')

def _btn_exito():
    return _btn_primario(VERDE_M, VERDE)

def _btn_peligro():
    return _btn_primario(NARANJA, '#BF360C')

def _grupo(titulo, color=AZUL):
    g = QGroupBox(titulo)
    g.setStyleSheet(
        f'QGroupBox {{font-weight:bold;color:{color};border:1.5px solid #CFD8DC;'
        f'border-radius:8px;margin-top:10px;padding:12px;}}'
        f'QGroupBox::title {{subcontrol-origin:margin;left:12px;padding:0 5px;}}')
    return g


# ═════════════════════════════════════════════════════════════════════════════
# WORKER THREAD — Descarga MDE
# ═════════════════════════════════════════════════════════════════════════════
class MDEWorker(QThread):
    log     = pyqtSignal(str)          # mensaje de progreso
    progreso= pyqtSignal(int)          # 0-100
    ok      = pyqtSignal(str, bool)    # (ruta_archivo, via_drive)
    error   = pyqtSignal(str)

    def __init__(self, dem_info, lon_min, lat_min, lon_max, lat_max,
                 scale_eff, output_path, project_id, gmail, epsg):
        super().__init__()
        self.dem_info    = dem_info
        self.lon_min     = lon_min
        self.lat_min     = lat_min
        self.lon_max     = lon_max
        self.lat_max     = lat_max
        self.scale_eff   = scale_eff
        self.output_path = output_path
        self.project_id  = project_id
        self.gmail       = gmail
        self.epsg        = epsg
        self._cancelar   = False

    def cancelar(self):
        self._cancelar = True

    def run(self):
        try:
            import ee
            # ── Conectar GEE ──────────────────────────────────────────────
            kwargs = {}
            if self.project_id.strip():
                kwargs['project'] = self.project_id.strip()
            self.log.emit(f"Conectando GEE — proyecto: {self.project_id or '(credenciales locales)'}")
            ee.Initialize(opt_url='https://earthengine.googleapis.com', **kwargs)
            self.log.emit("✔ Conexión GEE exitosa.")

            # ── Construir imagen ──────────────────────────────────────────
            dem_info = self.dem_info
            region   = ee.Geometry.Rectangle([
                self.lon_min, self.lat_min, self.lon_max, self.lat_max])

            cid  = dem_info['collection']
            band = dem_info['band']
            if dem_info['tipo'] == 'Image':
                imagen = ee.Image(cid).select(band).clip(region)
            else:
                imagen = (ee.ImageCollection(cid)
                            .filterBounds(region).mosaic()
                            .select(band).clip(region))

            # ── Estimar tamaño ─────────────────────────────────────────────
            lat_med  = (self.lat_min + self.lat_max) / 2.0
            cos_lat  = math.cos(math.radians(lat_med))
            ancho_m  = (self.lon_max - self.lon_min) * 111320.0 * cos_lat
            alto_m   = (self.lat_max - self.lat_min) * 111320.0
            px_x     = max(1, int(ancho_m / self.scale_eff))
            px_y     = max(1, int(alto_m  / self.scale_eff))
            total_px = px_x * px_y
            bpp      = DTYPE_BYTES.get(dem_info['dtype'], 4)
            peso_mb  = total_px * bpp / 1_048_576

            self.log.emit(f"DEM            : {dem_info['label']}")
            self.log.emit(f"Escala efectiva: {self.scale_eff} m")
            self.log.emit(f"Dimensión      : {px_y} × {px_x} px  ({total_px/1e6:.2f} MP | ~{peso_mb:.1f} MB)")

            self._descarga_tiled(ee, imagen, f'EPSG:{self.epsg}', total_px)

            self.progreso.emit(100)
            self.log.emit(f"✔ MDE guardado en:\n  {self.output_path}")
            self.ok.emit(self.output_path, False)

        except Exception as ex:
            self.error.emit(str(ex))

    def _descarga_tiled(self, ee, imagen, crs_str, total_px):
        import math, os
        try:
            from osgeo import gdal
        except ImportError:
            raise RuntimeError("La libreria GDAL no esta disponible en tu entorno QGIS.")

        limite_tile_px = 2_000_000  # 2 MP per tile
        num_tiles = math.ceil(total_px / limite_tile_px)

        if num_tiles <= 1:
            self.log.emit("Tamaño dentro del límite seguro -> descarga en un solo bloque.")
            region = ee.Geometry.Rectangle([self.lon_min, self.lat_min, self.lon_max, self.lat_max])
            self._descarga_bloque(ee, imagen, region, crs_str, self.output_path)
            return

        n_cols = max(1, math.ceil(math.sqrt(num_tiles * ((self.lon_max - self.lon_min) / max(1e-9, self.lat_max - self.lat_min)))))
        n_rows = max(1, math.ceil(num_tiles / n_cols))
        
        total_tiles = n_cols * n_rows
        self.log.emit(f"Area grande. Dividiendo en {n_cols}x{n_rows} = {total_tiles} cuadrantes para evitar limites de GEE...")
        
        lon_step = (self.lon_max - self.lon_min) / n_cols
        lat_step = (self.lat_max - self.lat_min) / n_rows
        
        temp_files = []
        k = 0
        
        for i in range(n_cols):
            for j in range(n_rows):
                if self._cancelar:
                    raise RuntimeError("Cancelado por el usuario.")
                k += 1
                self.log.emit(f"Descargando cuadrante {k}/{total_tiles}...")
                
                sub_lon_min = self.lon_min + i * lon_step
                sub_lon_max = self.lon_min + (i + 1) * lon_step
                sub_lat_min = self.lat_min + j * lat_step
                sub_lat_max = self.lat_min + (j + 1) * lat_step
                
                sub_region = ee.Geometry.Rectangle([sub_lon_min, sub_lat_min, sub_lon_max, sub_lat_max])
                temp_file = self.output_path.replace('.tif', f'_tile_{i}_{j}.tif')
                self._descarga_bloque(ee, imagen, sub_region, crs_str, temp_file)
                temp_files.append(temp_file)
                self.progreso.emit(int((k / total_tiles) * 100))
        
        if self._cancelar:
            raise RuntimeError("Cancelado por el usuario.")

        self.log.emit("Uniendo cuadrantes (Mosaico con GDAL)...")
        vrt_path = self.output_path.replace('.tif', '_temp.vrt')
        gdal.BuildVRT(vrt_path, temp_files)
        
        warp_options = gdal.WarpOptions(format='GTiff', creationOptions=['COMPRESS=LZW'])
        gdal.Warp(self.output_path, vrt_path, options=warp_options)
        
        self.log.emit("Limpiando archivos temporales...")
        if os.path.exists(vrt_path):
            os.remove(vrt_path)
        for tf in temp_files:
            if os.path.exists(tf):
                os.remove(tf)

    def _descarga_bloque(self, ee, imagen, region, crs_str, out_path):
        import zipfile, shutil, requests, os
        from urllib.parse import urlparse
        
        try:
            url = imagen.getDownloadURL({
                'scale' : self.scale_eff,
                'region': region,
                'format': 'GEO_TIFF',
                'crs'   : crs_str,
            })
        except Exception as ex:
            raise RuntimeError(f"Error al generar URL en GEE: {ex}")

        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            raise RuntimeError(f"URL invalida: esquema '{parsed.scheme}'")

        tmp = out_path + '_tmp.bin'
        try:
            with requests.get(url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(tmp, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=16384):
                        if self._cancelar:
                            raise RuntimeError("Cancelado por el usuario.")
                        if chunk:
                            f.write(chunk)
        except Exception as ex:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise RuntimeError(f"Error HTTP: {ex}")

        if zipfile.is_zipfile(tmp):
            with zipfile.ZipFile(tmp, 'r') as zf:
                tifs = [n for n in zf.namelist() if n.lower().endswith('.tif')]
                if not tifs:
                    raise RuntimeError("El ZIP no contiene archivos .tif")
                extracted = zf.extract(tifs[0], os.path.dirname(out_path) or '.')
            shutil.move(extracted, out_path)
            os.remove(tmp)
        else:
            shutil.move(tmp, out_path)


# ═════════════════════════════════════════════════════════════════════════════
# WORKERS GEE — inicialización / autenticación en background
# ═════════════════════════════════════════════════════════════════════════════
class _GeeInitWorker(QThread):
    """
    Ejecuta ee.Initialize() + una llamada simple de validación en background.
    Emite ok(proyecto) o error(msg).  Nunca bloquea el hilo principal de Qt.
    """
    ok    = pyqtSignal(str)   # proyecto
    error = pyqtSignal(str)   # mensaje de error

    def __init__(self, proyecto):
        super().__init__()
        self.proyecto = proyecto

    def run(self):
        try:
            import ee
            kwargs = {}
            if self.proyecto.strip():
                kwargs['project'] = self.proyecto.strip()
            ee.Initialize(opt_url='https://earthengine.googleapis.com', **kwargs)
            # Llamada mínima de validación (no descarga datos, solo confirma auth)
            ee.Image(1).getInfo()
            self.ok.emit(self.proyecto.strip())
        except Exception as ex:
            self.error.emit(str(ex))


class _GeeAuthWorker(QThread):
    """
    Ejecuta ee.Authenticate() en background.
    ee.Authenticate() abre el navegador y espera la aprobación del usuario.
    """
    ok    = pyqtSignal()
    error = pyqtSignal(str)

    def run(self):
        try:
            import ee
            ee.Authenticate()
            self.ok.emit()
        except Exception as ex:
            self.error.emit(str(ex))


# ═════════════════════════════════════════════════════════════════════════════
# DIÁLOGO PRINCIPAL
# ═════════════════════════════════════════════════════════════════════════════
class GeeSWATDialog(QDialog):

    SETTINGS_KEY = 'geeSWAT'

    def __init__(self, iface):
        super().__init__(iface.mainWindow())
        self.iface   = iface
        self.canvas  = iface.mapCanvas()
        self.setWindowTitle('geeSWAT con Google Earth Engine')
        self.setMinimumSize(900, 660)
        self.resize(980, 720)
        self.setStyleSheet('QDialog {background:#F5F7FA;}'
                           'QTabWidget::pane {border:1.5px solid #CFD8DC;border-radius:4px;}')

        # Estado interno
        self._aoi_geom     = None   # WKT en CRS nativo
        self._aoi_crs_epsg = None
        self._rubber       = None
        self._puntos_poly  = []
        self._tool_dibujo  = None
        self._click_count  = 0
        self._datos_ficha  = {}
        self._worker       = None
        self._gee_worker   = None   # worker for Init/Verify
        self._soil_worker  = None   # worker for SOIL download
        self._landuse_worker = None  # worker for LANDUSE download
        self._cn_worker    = None   # worker for CN download
        self._climaticos_worker = None # worker for CLIMATICOS download

        self._settings = QSettings(self.SETTINGS_KEY, self.SETTINGS_KEY)
        self._construir_ui()
        self._cargar_config()
        self._refrescar_capas()
        # Auto-connect after dialog is fully shown (non-blocking, 1 s delay)
        QTimer.singleShot(1000, self._auto_conectar_gee)

    # ══════════════════════════════════════════════════════════════════════════
    # UI PRINCIPAL
    # ══════════════════════════════════════════════════════════════════════════
    def _construir_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        # Barra de estado GEE
        self._barra_estado = QLabel('  ⬤  Sin conexión a GEE — Configure en la pestaña Config GEE')
        self._barra_estado.setStyleSheet(
            f'background:{ROJO};color:white;padding:7px 14px;font-size:11px;font-weight:bold;')
        self._barra_estado.setFixedHeight(34)
        main.addWidget(self._barra_estado)

        # Pestañas
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet(
            'QTabBar::tab {padding:9px 22px;font-size:11px;min-width:140px;}'
            f'QTabBar::tab:selected {{background:{AZUL_C};color:{AZUL};'
            f'font-weight:bold;border-bottom:3px solid {AZUL_M};}}')
        main.addWidget(self._tabs)

        self._tabs.addTab(self._tab_config(),   '⚙  Config GEE')
        self._tabs.addTab(self._tab_aoi(),      '🗺  Seleccionar AOI')
        self._tabs.addTab(self._tab_mde(),      '🏔  MDE')
        self._tabs.addTab(self._tab_soil(),     '🌍  SOIL')
        self._tabs.addTab(self._tab_landuse(),  '🌳  LANDUSE')
        self._tabs.addTab(self._tab_climaticos(), '⛅  CLIMÁTICOS')
        self._tabs.addTab(self._tab_cambio_climatico(), '🌍  Cambio climático')
        self._tabs.addTab(self._tab_cn(),       '📈  Curva Número')
        self._tabs.addTab(self._tab_acerca(),   'ℹ  Acerca de')

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA 1 — CONFIG GEE
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_config(self):
        w = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(24, 24, 24, 24); vl.setSpacing(16)

        # ── Conexión GEE ──────────────────────────────────────────────────────
        g1 = _grupo('🔑  Conexión a Google Earth Engine', AZUL)
        gl1 = QGridLayout(g1); gl1.setSpacing(10)

        gl1.addWidget(QLabel('Correo Gmail:'), 0, 0)
        self._inp_gmail = QLineEdit()
        self._inp_gmail.setPlaceholderText('tu.correo@gmail.com')
        self._inp_gmail.setMinimumHeight(32)
        gl1.addWidget(self._inp_gmail, 0, 1)

        gl1.addWidget(QLabel('ID del proyecto GEE:'), 1, 0)
        self._inp_project = QLineEdit()
        self._inp_project.setPlaceholderText('mi-proyecto-gee-123456')
        self._inp_project.setMinimumHeight(32)
        gl1.addWidget(self._inp_project, 1, 1)

        hl = QHBoxLayout()
        self._btn_auth = QPushButton('🔑  Autenticar con Google')
        self._btn_auth.setStyleSheet(_btn_primario())
        self._btn_auth.setMinimumHeight(38)
        self._btn_auth.setToolTip('Abre el navegador para autenticar con tu cuenta Google\ny guardar las credenciales para Earth Engine.')
        self._btn_auth.clicked.connect(self._autenticar_gee)

        self._btn_verificar = QPushButton('🔗  Verificar conexión')
        self._btn_verificar.setStyleSheet(_btn_secundario())
        self._btn_verificar.setMinimumHeight(38)
        self._btn_verificar.clicked.connect(self._verificar_gee)

        hl.addWidget(self._btn_auth)
        hl.addWidget(self._btn_verificar)
        hl.addStretch()
        gl1.addLayout(hl, 2, 0, 1, 2)

        self._lbl_gee_msg = QLabel('')
        self._lbl_gee_msg.setStyleSheet(f'color:{GRIS};font-size:10px;')
        gl1.addWidget(self._lbl_gee_msg, 3, 0, 1, 2)
        vl.addWidget(g1)

        # Info
        info = QLabel(
            '<b>Primera vez:</b> Necesitas una cuenta Gmail registrada en '
            '<a href="https://earthengine.google.com">earthengine.google.com</a> '
            'y un Google Cloud Project con la <b>Earth Engine API</b> habilitada.<br>'
            'Haz clic en <b>Autenticar con Google</b> → se abre el navegador → '
            'luego <b>Verificar conexión</b>.')
        info.setStyleSheet(f'background:{AZUL_C};color:{GRIS_OS};padding:12px;'
                           f'border-radius:6px;font-size:11px;border:1px solid #BBDEFB;')
        info.setOpenExternalLinks(True); info.setWordWrap(True)
        vl.addWidget(info)

        # ── Archivos de salida ────────────────────────────────────────────────
        g2 = _grupo('📁  Archivos de salida', VERDE)
        gl2 = QGridLayout(g2); gl2.setSpacing(10)

        gl2.addWidget(QLabel('Nombre del proyecto:'), 0, 0)
        self._inp_nombre_proy = QLineEdit()
        self._inp_nombre_proy.setPlaceholderText('Ej: Cuenca_Rimac_2026')
        self._inp_nombre_proy.setMinimumHeight(32)
        gl2.addWidget(self._inp_nombre_proy, 0, 1)

        gl2.addWidget(QLabel('Carpeta de salida:'), 1, 0)
        hl_c = QHBoxLayout()
        self._inp_carpeta = QLineEdit()
        self._inp_carpeta.setPlaceholderText('Selecciona la carpeta principal de salida...')
        self._inp_carpeta.setMinimumHeight(32)
        self._btn_carpeta = QPushButton('📁')
        self._btn_carpeta.setFixedSize(38, 34)
        self._btn_carpeta.setStyleSheet(_btn_secundario())
        self._btn_carpeta.clicked.connect(self._seleccionar_carpeta)
        hl_c.addWidget(self._inp_carpeta); hl_c.addWidget(self._btn_carpeta)
        gl2.addLayout(hl_c, 1, 1)

        nota = QLabel('ℹ️  Cada herramienta crea su propia subcarpeta (ej: <b>DEM/</b>, <b>SOIL/</b>) dentro de esta ruta.')
        nota.setStyleSheet(f'color:{GRIS};font-size:10px;')
        nota.setWordWrap(True)
        gl2.addWidget(nota, 2, 0, 1, 2)

        # ── Proyección del proyecto ──────────────────────────────────────────────
        g3 = _grupo('📏  Proyección del proyecto (CRS)', CYAN)
        gl3 = QGridLayout(g3); gl3.setSpacing(10)

        gl3.addWidget(QLabel('Código EPSG:'), 0, 0)
        self._inp_epsg = QLineEdit()
        self._inp_epsg.setPlaceholderText('Ej: 32718  (WGS84 / UTM zona 18S)')
        self._inp_epsg.setMinimumHeight(32)
        self._inp_epsg.setToolTip(
            'Código EPSG de la proyección de salida.\n'
            'Ejemplos:\n'
            '  32718 = WGS84 / UTM zona 18S (Perú sur)\n'
            '  32717 = WGS84 / UTM zona 17S (Perú centro)\n'
            '  32619 = WGS84 / UTM zona 19N (Colombia)\n'
            '  4326  = WGS84 geográfico (lat/lon)')
        gl3.addWidget(self._inp_epsg, 0, 1)

        nota_epsg = QLabel(
            'El DEM y el Suelo se descargarán en esta proyección.\n'
            'Deja en blanco para usar EPSG:4326 (WGS84 geográfico).')
        nota_epsg.setStyleSheet(f'color:{GRIS};font-size:10px;')
        nota_epsg.setWordWrap(True)
        gl3.addWidget(nota_epsg, 1, 0, 1, 2)
        vl.addWidget(g3)

        vl.addWidget(g2)

        self._btn_guardar = QPushButton('💾  Guardar configuración')
        self._btn_guardar.setStyleSheet(_btn_exito())
        self._btn_guardar.setMinimumHeight(40)
        self._btn_guardar.clicked.connect(self._guardar_config)
        vl.addWidget(self._btn_guardar)

        vl.addStretch()
        scroll.setWidget(inner)
        lv = QVBoxLayout(w); lv.setContentsMargins(0,0,0,0); lv.addWidget(scroll)
        return w

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA 2 — SELECCIONAR AOI
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_aoi(self):
        w   = QWidget()
        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(6)
        spl.setStyleSheet('QSplitter::handle {background:#CFD8DC;}')

        # ── Panel izquierdo (controles) ───────────────────────────────────────
        panel_izq = QWidget()
        panel_izq.setMinimumWidth(300)
        panel_izq.setMaximumWidth(400)
        vl = QVBoxLayout(panel_izq)
        vl.setContentsMargins(14, 14, 8, 14); vl.setSpacing(12)

        # -- Desde capa vectorial --
        g_capa = _grupo('📂  Desde capa vectorial', AZUL)
        vl_capa = QVBoxLayout(g_capa); vl_capa.setSpacing(8)

        hl_capa = QHBoxLayout()
        self._cmb_capas = QComboBox()
        self._cmb_capas.setMinimumHeight(30)
        btn_refrescar = QPushButton('↻')
        btn_refrescar.setFixedSize(30, 30)
        btn_refrescar.setStyleSheet(_btn_secundario())
        btn_refrescar.setToolTip('Refrescar lista de capas')
        btn_refrescar.clicked.connect(self._refrescar_capas)
        hl_capa.addWidget(self._cmb_capas, 1)
        hl_capa.addWidget(btn_refrescar)
        vl_capa.addLayout(hl_capa)

        self._btn_usar_capa = QPushButton('✔  Usar capa seleccionada')
        self._btn_usar_capa.setStyleSheet(_btn_primario())
        self._btn_usar_capa.setMinimumHeight(34)
        self._btn_usar_capa.clicked.connect(self._usar_capa)
        vl_capa.addWidget(self._btn_usar_capa)
        vl.addWidget(g_capa)

        # -- Dibujo interactivo --
        g_dibujo = _grupo('✏  Dibujo interactivo en el mapa', CYAN)
        vl_dib = QVBoxLayout(g_dibujo); vl_dib.setSpacing(8)

        lbl_inst = QLabel(
            '① Haz clic en el mapa para agregar vértices\n'
            '② Doble clic o clic derecho para cerrar el polígono')
        lbl_inst.setStyleSheet(f'color:{GRIS};font-size:10px;background:{CYAN_C};'
                               f'border-radius:5px;padding:6px;')
        vl_dib.addWidget(lbl_inst)

        self._btn_dibujar = QPushButton('✏  Activar dibujo de polígono')
        self._btn_dibujar.setStyleSheet(_btn_primario(CYAN, VERDE))
        self._btn_dibujar.setMinimumHeight(34)
        self._btn_dibujar.clicked.connect(self._activar_dibujo)
        vl_dib.addWidget(self._btn_dibujar)
        vl.addWidget(g_dibujo)

        # -- Acciones AOI --
        g_acc = _grupo('🔧  Acciones', GRIS)
        vl_acc = QVBoxLayout(g_acc); vl_acc.setSpacing(6)

        self._btn_zoom_aoi = QPushButton('🔍  Zoom al AOI')
        self._btn_zoom_aoi.setStyleSheet(_btn_secundario())
        self._btn_zoom_aoi.setMinimumHeight(32)
        self._btn_zoom_aoi.setEnabled(False)
        self._btn_zoom_aoi.clicked.connect(self._zoom_aoi)
        vl_acc.addWidget(self._btn_zoom_aoi)

        self._btn_limpiar_aoi = QPushButton('🗑  Limpiar AOI')
        self._btn_limpiar_aoi.setStyleSheet(_btn_peligro())
        self._btn_limpiar_aoi.setMinimumHeight(32)
        self._btn_limpiar_aoi.setEnabled(False)
        self._btn_limpiar_aoi.clicked.connect(self._limpiar_aoi)
        vl_acc.addWidget(self._btn_limpiar_aoi)
        vl.addWidget(g_acc)

        vl.addStretch()

        # Indicador de estado AOI (abajo del panel)
        self._lbl_aoi_estado = QLabel('Sin AOI definida')
        self._lbl_aoi_estado.setAlignment(Qt.AlignCenter)
        self._lbl_aoi_estado.setWordWrap(True)
        self._lbl_aoi_estado.setStyleSheet(
            f'background:#ECEFF1;color:{GRIS};border-radius:6px;'
            f'padding:6px;font-size:10px;')
        vl.addWidget(self._lbl_aoi_estado)

        spl.addWidget(panel_izq)

        # ── Panel derecho (ficha AOI) ──────────────────────────────────────────
        panel_der = QWidget()
        panel_der.setMinimumWidth(340)
        vl_der = QVBoxLayout(panel_der)
        vl_der.setContentsMargins(8, 14, 14, 14); vl_der.setSpacing(8)

        lbl_ficha_h = QLabel('📍  Ficha del Área de Interés')
        lbl_ficha_h.setStyleSheet(
            f'background:{AZUL_M};color:white;padding:9px 14px;'
            f'font-weight:bold;font-size:12px;border-radius:6px 6px 0 0;')
        vl_der.addWidget(lbl_ficha_h)

        self._ficha_widget = QWidget()
        self._ficha_widget.setStyleSheet(
            'background:white;border:1.5px solid #CFD8DC;'
            'border-top:none;border-radius:0 0 6px 6px;')
        self._ficha_layout = QVBoxLayout(self._ficha_widget)
        self._ficha_layout.setContentsMargins(14, 12, 14, 12)
        self._ficha_layout.setSpacing(0)

        self._lbl_sin_aoi = QLabel('Define un área de interés\npara ver su ficha geométrica.')
        self._lbl_sin_aoi.setAlignment(Qt.AlignCenter)
        self._lbl_sin_aoi.setStyleSheet(f'color:#9E9E9E;font-size:11px;padding:40px;')
        self._ficha_layout.addWidget(self._lbl_sin_aoi)
        self._ficha_layout.addStretch()
        vl_der.addWidget(self._ficha_widget, 1)

        # Alerta de tamaño
        self._lbl_alerta_area = QLabel('')
        self._lbl_alerta_area.setWordWrap(True)
        self._lbl_alerta_area.setStyleSheet('font-size:10px;padding:4px;')
        self._lbl_alerta_area.setVisible(False)
        vl_der.addWidget(self._lbl_alerta_area)

        # Botón ir a MDE
        self._btn_ir_mde = QPushButton('🏔  Ir a descargar MDE →')
        self._btn_ir_mde.setStyleSheet(_btn_exito())
        self._btn_ir_mde.setMinimumHeight(38)
        self._btn_ir_mde.setEnabled(False)
        self._btn_ir_mde.clicked.connect(lambda: self._tabs.setCurrentIndex(2))
        vl_der.addWidget(self._btn_ir_mde)

        spl.addWidget(panel_der)
        spl.setSizes([340, 540])

        hl_main = QHBoxLayout(w)
        hl_main.setContentsMargins(0, 0, 0, 0)
        hl_main.addWidget(spl)
        return w

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA 3 — MDE
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_mde(self):
        w   = QWidget()
        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(6)
        spl.setStyleSheet('QSplitter::handle {background:#CFD8DC;}')

        # ── Panel izquierdo (parámetros) ──────────────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setMinimumWidth(300)
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(14, 14, 8, 14); vl.setSpacing(14)

        # ── AOI actual ────────────────────────────────────────────────────────
        g_aoi = _grupo('🗺  Área de Interés', AZUL)
        vl_aoi = QVBoxLayout(g_aoi); vl_aoi.setSpacing(6)

        self._lbl_mde_aoi = QLabel('Sin AOI definida — ve a la pestaña "Seleccionar AOI"')
        self._lbl_mde_aoi.setWordWrap(True)
        self._lbl_mde_aoi.setStyleSheet(
            f'background:{ROJO_C};color:{ROJO};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_aoi.addWidget(self._lbl_mde_aoi)

        btn_ir_aoi = QPushButton('← Definir AOI')
        btn_ir_aoi.setStyleSheet(_btn_secundario())
        btn_ir_aoi.setMinimumHeight(30)
        btn_ir_aoi.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
        vl_aoi.addWidget(btn_ir_aoi)
        vl.addWidget(g_aoi)

        # ── Fuente DEM ────────────────────────────────────────────────────────
        g_dem = _grupo('🛰  Fuente del DEM', AZUL_M)
        vl_dem = QVBoxLayout(g_dem); vl_dem.setSpacing(10)

        self._cmb_dem = QComboBox()
        self._cmb_dem.setMinimumHeight(32)
        for d in GEE_DEMS:
            self._cmb_dem.addItem(d['label'])
        self._cmb_dem.currentIndexChanged.connect(self._actualizar_info_dem)
        vl_dem.addWidget(self._cmb_dem)

        self._lbl_dem_desc = QLabel('')
        self._lbl_dem_desc.setWordWrap(True)
        self._lbl_dem_desc.setStyleSheet(
            f'background:{AZUL_C};color:{GRIS_OS};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_dem.addWidget(self._lbl_dem_desc)

        # Escala
        gl_esc = QGridLayout(); gl_esc.setSpacing(8)
        gl_esc.addWidget(QLabel('Escala de salida (m):'), 0, 0)
        self._spn_escala = QSpinBox()
        self._spn_escala.setRange(0, 10000)
        self._spn_escala.setValue(0)
        self._spn_escala.setMinimumHeight(30)
        self._spn_escala.setSpecialValueText('0  (resolución nativa del DEM)')
        self._spn_escala.setToolTip(
            '0 = resolución nativa del DEM seleccionado.\n'
            'Ejemplo: SRTM → 30 m, MERIT/HydroSHEDS → 90 m.')
        self._spn_escala.valueChanged.connect(self._actualizar_info_dem)
        gl_esc.addWidget(self._spn_escala, 0, 1)

        self._lbl_escala_info = QLabel('')
        self._lbl_escala_info.setStyleSheet(f'color:{GRIS};font-size:10px;')
        gl_esc.addWidget(self._lbl_escala_info, 1, 0, 1, 2)
        vl_dem.addLayout(gl_esc)
        vl.addWidget(g_dem)
        # NOTE: _actualizar_info_dem() is called AFTER _lbl_ruta_salida is created below

        # ── Salida ────────────────────────────────────────────────────────────
        g_sal = _grupo('💾  Archivo de salida', VERDE)
        vl_sal = QVBoxLayout(g_sal); vl_sal.setSpacing(8)

        nota_sal = QLabel(
            'El archivo se guarda automáticamente en:\n'
            '<carpeta_salida> / <b>DEM</b> / <nombre_dem>.tif')
        nota_sal.setTextFormat(Qt.RichText)
        nota_sal.setWordWrap(True)
        nota_sal.setStyleSheet(
            f'background:{VERDE_C};color:{GRIS_OS};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_sal.addWidget(nota_sal)

        self._lbl_ruta_salida = QLabel('Ruta: (configura la carpeta de salida en Config GEE)')
        self._lbl_ruta_salida.setWordWrap(True)
        self._lbl_ruta_salida.setStyleSheet(
            f'color:{GRIS};font-size:10px;font-style:italic;')
        vl_sal.addWidget(self._lbl_ruta_salida)
        vl.addWidget(g_sal)
        # Now all MDE widgets exist — safe to initialise DEM info
        self._actualizar_info_dem()

        # ── Botones de ejecución ──────────────────────────────────────────────
        self._btn_descargar = QPushButton('▶  Descargar MDE')
        self._btn_descargar.setStyleSheet(_btn_exito())
        self._btn_descargar.setMinimumHeight(42)
        self._btn_descargar.setFont(QFont('Arial', 12, QFont.Bold))
        self._btn_descargar.clicked.connect(self._ejecutar_descarga)
        vl.addWidget(self._btn_descargar)

        self._btn_cancelar_mde = QPushButton('⏹  Cancelar')
        self._btn_cancelar_mde.setStyleSheet(_btn_secundario())
        self._btn_cancelar_mde.setMinimumHeight(36)
        self._btn_cancelar_mde.setEnabled(False)
        self._btn_cancelar_mde.clicked.connect(self._cancelar_descarga)
        vl.addWidget(self._btn_cancelar_mde)

        self._progreso_mde = QProgressBar()
        self._progreso_mde.setStyleSheet(
            f'QProgressBar {{height:18px;border-radius:5px;background:#E0E0E0;}}'
            f'QProgressBar::chunk {{background:{VERDE_M};border-radius:5px;}}')
        self._progreso_mde.setValue(0)
        vl.addWidget(self._progreso_mde)

        vl.addStretch()
        scroll.setWidget(inner)
        spl.addWidget(scroll)

        # ── Panel derecho (log) ───────────────────────────────────────────────
        panel_log = QWidget()
        panel_log.setMinimumWidth(320)
        vl_log = QVBoxLayout(panel_log)
        vl_log.setContentsMargins(8, 14, 14, 14); vl_log.setSpacing(8)

        lbl_log_h = QLabel('📋  Registro de descarga')
        lbl_log_h.setStyleSheet(
            f'background:{GRIS_OS};color:white;padding:9px 14px;'
            f'font-weight:bold;font-size:12px;border-radius:6px 6px 0 0;')
        vl_log.addWidget(lbl_log_h)

        self._txt_log = QTextEdit()
        self._txt_log.setReadOnly(True)
        self._txt_log.setStyleSheet(
            'QTextEdit {background:#1E272E;color:#ECF0F1;font-family:Consolas,monospace;'
            'font-size:10px;border:1.5px solid #CFD8DC;border-top:none;'
            'border-radius:0 0 6px 6px;padding:8px;}')
        self._txt_log.setPlaceholderText('Los mensajes de progreso aparecerán aquí...')
        vl_log.addWidget(self._txt_log, 1)

        btn_limpiar_log = QPushButton('🗑  Limpiar registro')
        btn_limpiar_log.setStyleSheet(_btn_secundario())
        btn_limpiar_log.setMinimumHeight(30)
        btn_limpiar_log.clicked.connect(self._txt_log.clear)
        vl_log.addWidget(btn_limpiar_log)

        spl.addWidget(panel_log)
        spl.setSizes([380, 500])

        hl = QHBoxLayout(w); hl.setContentsMargins(0,0,0,0); hl.addWidget(spl)

        # Actualizar ruta al cambiar la pestaña
        self._tabs_ref = None
        return w

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA 4 — SOIL
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_soil(self):
        from .Script.soil_descargar_suelo import SOIL_SOURCES
        self._SOIL_SOURCES = SOIL_SOURCES

        w   = QWidget()
        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(6)
        spl.setStyleSheet('QSplitter::handle {background:#CFD8DC;}')

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setMinimumWidth(310)
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(14, 14, 8, 14); vl.setSpacing(14)

        # AOI
        g_aoi = _grupo('Mapa  Area de Interes', AZUL)
        vl_aoi = QVBoxLayout(g_aoi); vl_aoi.setSpacing(6)
        self._lbl_soil_aoi = QLabel(
            'Sin AOI definida - ve a la pestana "Seleccionar AOI"')
        self._lbl_soil_aoi.setWordWrap(True)
        self._lbl_soil_aoi.setStyleSheet(
            f'background:{ROJO_C};color:{ROJO};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_aoi.addWidget(self._lbl_soil_aoi)
        btn_ir_aoi2 = QPushButton('Ir a Definir AOI')
        btn_ir_aoi2.setStyleSheet(_btn_secundario())
        btn_ir_aoi2.setMinimumHeight(30)
        btn_ir_aoi2.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
        vl_aoi.addWidget(btn_ir_aoi2)
        vl.addWidget(g_aoi)

        # Fuente de suelo
        g_src = _grupo('Tipo de Suelo - Fuente GEE', AZUL_M)
        vl_src = QVBoxLayout(g_src); vl_src.setSpacing(10)

        self._cmb_soil = QComboBox()
        self._cmb_soil.setMinimumHeight(32)
        for s in SOIL_SOURCES:
            self._cmb_soil.addItem(s['label'])
        self._cmb_soil.currentIndexChanged.connect(self._actualizar_info_soil)
        vl_src.addWidget(self._cmb_soil)

        self._lbl_soil_desc = QLabel('')
        self._lbl_soil_desc.setWordWrap(True)
        self._lbl_soil_desc.setOpenExternalLinks(True)
        self._lbl_soil_desc.setStyleSheet(
            f'background:{AZUL_C};color:{GRIS_OS};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_src.addWidget(self._lbl_soil_desc)

        gl_s = QGridLayout(); gl_s.setSpacing(8)
        gl_s.addWidget(QLabel('Escala de salida (m):'), 0, 0)
        self._spn_soil_escala = QSpinBox()
        self._spn_soil_escala.setRange(0, 10000)
        self._spn_soil_escala.setValue(0)
        self._spn_soil_escala.setMinimumHeight(30)
        self._spn_soil_escala.setSpecialValueText('0  (resolucion nativa del mapa de suelos)')
        self._spn_soil_escala.valueChanged.connect(self._actualizar_info_soil)
        gl_s.addWidget(self._spn_soil_escala, 0, 1)
        self._lbl_soil_esc_info = QLabel('')
        self._lbl_soil_esc_info.setStyleSheet(f'color:{GRIS};font-size:10px;')
        gl_s.addWidget(self._lbl_soil_esc_info, 1, 0, 1, 2)
        vl_src.addLayout(gl_s)
        vl.addWidget(g_src)
        self._actualizar_info_soil()

        # Salida
        g_sal = _grupo('Archivo de salida', VERDE)
        vl_sal = QVBoxLayout(g_sal); vl_sal.setSpacing(8)
        nota2 = QLabel(
            'Los archivos se guardan en:\n'
            'carpeta_salida/SOIL/nombre.tif\n'
            'carpeta_salida/SOIL/nombre_Soil_lookup.csv')
        nota2.setWordWrap(True)
        nota2.setStyleSheet(
            f'background:{VERDE_C};color:{GRIS_OS};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_sal.addWidget(nota2)
        self._lbl_soil_ruta = QLabel(
            'Ruta: (configura la carpeta de salida en Config GEE)')
        self._lbl_soil_ruta.setWordWrap(True)
        self._lbl_soil_ruta.setStyleSheet(
            f'color:{GRIS};font-size:10px;font-style:italic;')
        vl_sal.addWidget(self._lbl_soil_ruta)
        
        self._chk_soil_vector = QCheckBox("Generar capa vectorial automáticamente")
        self._chk_soil_vector.setChecked(True)
        vl_sal.addWidget(self._chk_soil_vector)
        
        vl.addWidget(g_sal)

        # CRS info
        self._lbl_soil_crs = QLabel('CRS: (configura el EPSG en Config GEE)')
        self._lbl_soil_crs.setStyleSheet(
            f'background:#FFF8E1;color:#F57F17;padding:6px;'
            f'border-radius:5px;font-size:10px;border:1px solid #F57F17;')
        self._lbl_soil_crs.setWordWrap(True)
        vl.addWidget(self._lbl_soil_crs)

        self._tabs.currentChanged.connect(self._on_tab_soil_visible)

        self._btn_soil_desc = QPushButton('Descargar Suelo + Soil_lookup.csv')
        self._btn_soil_desc.setStyleSheet(_btn_exito())
        self._btn_soil_desc.setMinimumHeight(42)
        self._btn_soil_desc.setFont(QFont('Arial', 11, QFont.Bold))
        self._btn_soil_desc.clicked.connect(self._ejecutar_soil)
        vl.addWidget(self._btn_soil_desc)

        self._btn_soil_cancel = QPushButton('Cancelar')
        self._btn_soil_cancel.setStyleSheet(_btn_secundario())
        self._btn_soil_cancel.setMinimumHeight(34)
        self._btn_soil_cancel.setEnabled(False)
        self._btn_soil_cancel.clicked.connect(self._cancelar_soil)
        vl.addWidget(self._btn_soil_cancel)

        self._prog_soil = QProgressBar()
        self._prog_soil.setStyleSheet(
            f'QProgressBar {{height:18px;border-radius:5px;background:#E0E0E0;}}'
            f'QProgressBar::chunk {{background:{VERDE_M};border-radius:5px;}}')
        self._prog_soil.setValue(0)
        vl.addWidget(self._prog_soil)
        vl.addStretch()
        scroll.setWidget(inner)
        spl.addWidget(scroll)

        panel_log = QWidget(); panel_log.setMinimumWidth(320)
        vl_log = QVBoxLayout(panel_log)
        vl_log.setContentsMargins(8, 14, 14, 14); vl_log.setSpacing(8)
        lbl_h = QLabel('Registro de descarga de suelo')
        lbl_h.setStyleSheet(
            f'background:{GRIS_OS};color:white;padding:9px 14px;'
            f'font-weight:bold;font-size:12px;border-radius:6px 6px 0 0;')
        vl_log.addWidget(lbl_h)
        self._txt_soil_log = QTextEdit()
        self._txt_soil_log.setReadOnly(True)
        self._txt_soil_log.setStyleSheet(
            'QTextEdit {background:#1E272E;color:#ECF0F1;'
            'font-family:Consolas,monospace;font-size:10px;'
            'border:1.5px solid #CFD8DC;border-top:none;'
            'border-radius:0 0 6px 6px;padding:8px;}')
        self._txt_soil_log.setPlaceholderText('Los mensajes apareceran aqui...')
        vl_log.addWidget(self._txt_soil_log, 1)
        btn_clear = QPushButton('Limpiar registro')
        btn_clear.setStyleSheet(_btn_secundario())
        btn_clear.setMinimumHeight(30)
        btn_clear.clicked.connect(self._txt_soil_log.clear)
        vl_log.addWidget(btn_clear)

        spl.addWidget(panel_log)
        spl.setSizes([380, 500])
        hl = QHBoxLayout(w); hl.setContentsMargins(0,0,0,0); hl.addWidget(spl)
        return w

    def _on_tab_soil_visible(self, idx):
        if idx != 3:
            return
        if not hasattr(self, '_lbl_soil_ruta'):
            return
        carpeta = self._inp_carpeta.text().strip()
        epsg    = self._get_epsg()
        if carpeta:
            soil_dir = os.path.join(carpeta, 'SOIL')
            self._lbl_soil_ruta.setText(f'Ruta de salida: {soil_dir}')
        else:
            self._lbl_soil_ruta.setText('Ruta: (configura la carpeta de salida en Config GEE)')
        if epsg:
            self._lbl_soil_crs.setText(f'CRS de salida: EPSG:{epsg}')
            self._lbl_soil_crs.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};padding:6px;'
                f'border-radius:5px;font-size:10px;border:1px solid {VERDE};')
        else:
            self._lbl_soil_crs.setText(
                'CRS: EPSG:4326 sin configurar - '
                'configura el EPSG en Config GEE para reproyectar.')
            self._lbl_soil_crs.setStyleSheet(
                f'background:#FFF8E1;color:#F57F17;padding:6px;'
                f'border-radius:5px;font-size:10px;border:1px solid #F57F17;')
        if self._aoi_geom and self._datos_ficha:
            area = self._datos_ficha.get('Area', '?')
            self._lbl_soil_aoi.setText(
                f'AOI definida - Area: {area} | '
                f'Bbox: [{self._datos_ficha.get("Bbox Oeste","?")} , '
                f'{self._datos_ficha.get("Bbox Sur","?")} , '
                f'{self._datos_ficha.get("Bbox Este","?")} , '
                f'{self._datos_ficha.get("Bbox Norte","?")}]')
            self._lbl_soil_aoi.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};padding:8px;'
                f'border-radius:5px;font-size:10px;')
        else:
            self._lbl_soil_aoi.setText(
                'Sin AOI definida - ve a la pestana "Seleccionar AOI"')
            self._lbl_soil_aoi.setStyleSheet(
                f'background:{ROJO_C};color:{ROJO};padding:8px;'
                f'border-radius:5px;font-size:10px;')

    def _actualizar_info_soil(self):
        if not hasattr(self, '_cmb_soil'):
            return
        idx = self._cmb_soil.currentIndex()
        if idx < 0 or idx >= len(self._SOIL_SOURCES):
            return
        s      = self._SOIL_SOURCES[idx]
        esc    = self._spn_soil_escala.value()
        esc_ef = esc if esc > 0 else s['scale']
        self._lbl_soil_desc.setText(
            f'<b>Asset GEE:</b> {s["asset"]}<br>'
            f'<b>Tipo:</b> {s["tipo"].capitalize()}  |  '
            f'<b>Res. nativa:</b> {s["scale"]} m  |  '
            f'<b>Campo ID:</b> {s["campo"]}<br>'
            f'<b>Info:</b> {s["desc"]}<br>'
            f'<b>Escala efectiva:</b> {esc_ef} m' +
            (' <i>(nativa)</i>' if esc == 0 else ''))
        self._lbl_soil_esc_info.setText(
            f'0 = resolucion nativa ({s["scale"]} m). Efectiva: {esc_ef} m.')

    def _ejecutar_soil(self):
        from .Script.soil_descargar_suelo import SoilWorker
        if not self._aoi_geom:
            QMessageBox.warning(self, 'geeSWAT',
                'Define primero el area de interes en "Seleccionar AOI".')
            self._tabs.setCurrentIndex(1)
            return
        carpeta = self._inp_carpeta.text().strip()
        if not carpeta:
            QMessageBox.warning(self, 'geeSWAT',
                'Configura la carpeta de salida en "Config GEE".')
            self._tabs.setCurrentIndex(0)
            return
        proyecto = self._inp_project.text().strip()
        idx_src  = self._cmb_soil.currentIndex()
        src      = self._SOIL_SOURCES[idx_src]
        esc      = self._spn_soil_escala.value()
        esc_ef   = esc if esc > 0 else src['scale']
        epsg     = self._get_epsg() or 4326
        bbox = self._datos_ficha.get('_bbox_wgs84')
        if not bbox:
            from qgis.core import (QgsGeometry, QgsCoordinateReferenceSystem,
                                   QgsCoordinateTransform)
            geom  = QgsGeometry.fromWkt(self._aoi_geom)
            crs   = QgsCoordinateReferenceSystem(f'EPSG:{self._aoi_crs_epsg}')
            crs84 = QgsCoordinateReferenceSystem('EPSG:4326')
            if crs != crs84:
                geom.transform(QgsCoordinateTransform(crs, crs84, QgsProject.instance()))
            bb   = geom.boundingBox()
            bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
        lon_min, lat_min, lon_max, lat_max = bbox
        soil_dir = os.path.join(carpeta, 'SOIL')
        os.makedirs(soil_dir, exist_ok=True)
        nombre_proy = self._inp_nombre_proy.text().strip()
        ts      = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        id_str  = f'{src["id"]}_{src["year"]}' if src.get('has_year') else src['id']
        fname   = f'{nombre_proy}_{id_str}_{ts}' if nombre_proy else f'{id_str}_{ts}'
        out_tif = os.path.join(soil_dir, fname + '.tif')
        out_csv = os.path.join(soil_dir, fname + '_Soil_lookup.csv')
        self._btn_soil_desc.setEnabled(False)
        self._btn_soil_cancel.setEnabled(True)
        self._prog_soil.setValue(0)
        self._txt_soil_log.clear()
        self._soil_log(f'Fuente          : {src["label"]}')
        self._soil_log(f'Escala efectiva : {esc_ef} m')
        self._soil_log(f'Proyeccion      : EPSG:{epsg}')
        self._soil_log(f'Bbox WGS84      : [{lon_min:.5f}, {lat_min:.5f}, {lon_max:.5f}, {lat_max:.5f}]')
        self._soil_log(f'Raster salida   : {out_tif}')
        self._soil_log(f'CSV salida      : {out_csv}')
        self._soil_log('─' * 50)
        self._soil_worker = SoilWorker(
            src, lon_min, lat_min, lon_max, lat_max,
            esc_ef, epsg, out_tif, out_csv, proyecto)
        self._soil_worker.log.connect(self._soil_log)
        self._soil_worker.progreso.connect(self._prog_soil.setValue)
        self._soil_worker.ok.connect(self._soil_completado)
        self._soil_worker.error.connect(self._soil_error)
        self._soil_worker.finished.connect(self._soil_worker_terminado)
        self._soil_worker.start()

    def _cancelar_soil(self):
        if self._soil_worker and self._soil_worker.isRunning():
            self._soil_worker.cancelar()
            self._soil_log('Cancelando...')

    def _soil_log(self, msg):
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._txt_soil_log.append(
            f'<span style="color:#95A5A6">[{ts}]</span> {msg}')
        sb = self._txt_soil_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _soil_completado(self, ruta_tif, ruta_csv, via_drive):
        if via_drive:
            self._soil_log('Exportacion a Google Drive completada.')
            QMessageBox.information(self, 'geeSWAT - SOIL Completado',
                'El suelo se exporto a Google Drive.\n'
                'Descargalo desde drive.google.com')
        else:
            self._soil_log(f'Raster guardado: {ruta_tif}')
            if ruta_csv:
                self._soil_log(f'CSV guardado   : {ruta_csv}')
            try:
                src    = self._SOIL_SOURCES[self._cmb_soil.currentIndex()]
                rlayer = QgsRasterLayer(ruta_tif, f'SOIL - {src["id"]}')
                if rlayer.isValid():
                    if src["id"] == 'FAO':
                        self._aplicar_simbologia_fao(rlayer)
                    elif src["id"] == 'DSoilMap':
                        self._aplicar_simbologia_dsoilmap(rlayer)
                        
                    QgsProject.instance().addMapLayer(rlayer)
                    self._soil_log('Raster cargado en QGIS.')
                    
                    import os
                    out_dir = os.path.dirname(ruta_tif)
                    file_prefix = os.path.splitext(os.path.basename(ruta_tif))[0] + "_vector"
                    
                    if hasattr(self, '_chk_soil_vector') and self._chk_soil_vector.isChecked():
                        extra_dict = None
                        if src["id"] == 'DSoilMap':
                            from .Script.simbologia_suelo_dsoilmap import CLASES_SUELO_DSOILMAP
                            extra_dict = CLASES_SUELO_DSOILMAP
                            
                        self._generar_vector_desde_raster(rlayer, out_dir, file_prefix, self._soil_log, extra_dict)
                    else:
                        self._soil_log('Generación de vector omitida (Checkbox desmarcado).')

            except Exception as ex:
                self._soil_log(f'No se pudo cargar en QGIS: {ex}')
            QMessageBox.information(self, 'geeSWAT - SOIL Completado',
                f'Raster de suelo:\n  {ruta_tif}\n\n'
                f'Tabla Soil_lookup.csv:\n  {ruta_csv or "(no generada)"}')

    def _soil_error(self, msg):
        self._soil_log(f'ERROR: {msg}')
        QMessageBox.critical(self, 'geeSWAT - Error SOIL', msg)

    def _soil_worker_terminado(self):
        self._btn_soil_desc.setEnabled(True)
        self._btn_soil_cancel.setEnabled(False)

    def _aplicar_simbologia_fao(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor
        from .Script.simbologia_suelo_fao import CLASES_SUELO_FAO

        classes = []
        for val, hex_color, name in CLASES_SUELO_FAO:
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _aplicar_simbologia_dsoilmap(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor
        from .Script.simbologia_suelo_dsoilmap import CLASES_SUELO_DSOILMAP

        classes = []
        for val, info in sorted(CLASES_SUELO_DSOILMAP.items()):
            name = info.get('name', 'Desconocido')
            hex_color = info.get('hex', '#000000')
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA 5 - LANDUSE
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_landuse(self):
        from .Script.landuse_descargar_landuse import LANDUSE_SOURCES
        self._LANDUSE_SOURCES = LANDUSE_SOURCES

        w   = QWidget()
        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(6)
        spl.setStyleSheet('QSplitter::handle {background:#CFD8DC;}')

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setMinimumWidth(310)
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(14, 14, 8, 14); vl.setSpacing(14)

        # AOI
        g_aoi = _grupo('Area de Interes (AOI)', AZUL)
        vl_aoi = QVBoxLayout(g_aoi); vl_aoi.setSpacing(6)
        self._lbl_lu_aoi = QLabel(
            'Sin AOI definida - ve a la pestana "Seleccionar AOI"')
        self._lbl_lu_aoi.setWordWrap(True)
        self._lbl_lu_aoi.setStyleSheet(
            f'background:{ROJO_C};color:{ROJO};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_aoi.addWidget(self._lbl_lu_aoi)
        btn_ir = QPushButton('Ir a Definir AOI')
        btn_ir.setStyleSheet(_btn_secundario())
        btn_ir.setMinimumHeight(30)
        btn_ir.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
        vl_aoi.addWidget(btn_ir)
        vl.addWidget(g_aoi)

        # Fuente de uso del suelo
        g_src = _grupo('Fuente de Uso del Suelo (LANDUSE)', AZUL_M)
        vl_src = QVBoxLayout(g_src); vl_src.setSpacing(10)

        self._cmb_lu = QComboBox()
        self._cmb_lu.setMinimumHeight(32)
        for s in LANDUSE_SOURCES:
            self._cmb_lu.addItem(s['label'])
        self._cmb_lu.currentIndexChanged.connect(self._actualizar_info_landuse)
        vl_src.addWidget(self._cmb_lu)

        h_year = QHBoxLayout()
        self._lbl_lu_year = QLabel('Año:')
        self._spn_lu_year = QSpinBox()
        self._spn_lu_year.setMinimumHeight(30)
        self._spn_lu_year.valueChanged.connect(self._actualizar_info_landuse)
        self._lbl_lu_year.hide()
        self._spn_lu_year.hide()
        h_year.addWidget(self._lbl_lu_year)
        h_year.addWidget(self._spn_lu_year)
        vl_src.addLayout(h_year)

        self._lbl_lu_desc = QLabel('')
        self._lbl_lu_desc.setWordWrap(True)
        self._lbl_lu_desc.setStyleSheet(
            f'background:{AZUL_C};color:{GRIS_OS};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_src.addWidget(self._lbl_lu_desc)

        # Escala
        gl_s = QGridLayout(); gl_s.setSpacing(8)
        gl_s.addWidget(QLabel('Escala de salida (m):'), 0, 0)
        self._spn_lu_escala = QSpinBox()
        self._spn_lu_escala.setRange(0, 10000)
        self._spn_lu_escala.setValue(0)
        self._spn_lu_escala.setMinimumHeight(30)
        self._spn_lu_escala.setSpecialValueText('0  (resolucion nativa del mapa)')
        self._spn_lu_escala.setToolTip(
            '0 = resolucion nativa.\n'
            'Copernicus 2019: 100 m  |  ESA GlobCover 2009: 300 m')
        self._spn_lu_escala.valueChanged.connect(self._actualizar_info_landuse)
        gl_s.addWidget(self._spn_lu_escala, 0, 1)
        self._lbl_lu_esc_info = QLabel('')
        self._lbl_lu_esc_info.setStyleSheet(f'color:{GRIS};font-size:10px;')
        gl_s.addWidget(self._lbl_lu_esc_info, 1, 0, 1, 2)
        vl_src.addLayout(gl_s)
        vl.addWidget(g_src)
        self._actualizar_info_landuse()

        # Salida
        g_sal = _grupo('Archivo de salida', VERDE)
        vl_sal = QVBoxLayout(g_sal); vl_sal.setSpacing(8)
        nota2 = QLabel(
            'Los archivos se guardan en:\n'
            'carpeta_salida/LANDUSE/nombre.tif\n'
            'carpeta_salida/LANDUSE/nombre_Landuse_lookup.csv')
        nota2.setWordWrap(True)
        nota2.setStyleSheet(
            f'background:{VERDE_C};color:{GRIS_OS};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_sal.addWidget(nota2)
        self._lbl_lu_ruta = QLabel(
            'Ruta: (configura la carpeta de salida en Config GEE)')
        self._lbl_lu_ruta.setWordWrap(True)
        self._lbl_lu_ruta.setStyleSheet(
            f'color:{GRIS};font-size:10px;font-style:italic;')
        vl_sal.addWidget(self._lbl_lu_ruta)
        
        self._chk_lu_vector = QCheckBox("Generar capa vectorial automáticamente")
        self._chk_lu_vector.setChecked(True)
        vl_sal.addWidget(self._chk_lu_vector)
        
        vl.addWidget(g_sal)

        # CRS info
        self._lbl_lu_crs = QLabel('CRS: (configura el EPSG en Config GEE)')
        self._lbl_lu_crs.setStyleSheet(
            f'background:#FFF8E1;color:#F57F17;padding:6px;'
            f'border-radius:5px;font-size:10px;border:1px solid #F57F17;')
        self._lbl_lu_crs.setWordWrap(True)
        vl.addWidget(self._lbl_lu_crs)

        # Actualizar al cambiar de pestana (idx=4)
        self._tabs.currentChanged.connect(self._on_tab_landuse_visible)

        self._btn_lu_desc = QPushButton('Descargar LANDUSE + Landuse_lookup.csv')
        self._btn_lu_desc.setStyleSheet(_btn_exito())
        self._btn_lu_desc.setMinimumHeight(42)
        self._btn_lu_desc.setFont(QFont('Arial', 11, QFont.Bold))
        self._btn_lu_desc.clicked.connect(self._ejecutar_landuse)
        vl.addWidget(self._btn_lu_desc)

        self._btn_lu_cancel = QPushButton('Cancelar')
        self._btn_lu_cancel.setStyleSheet(_btn_secundario())
        self._btn_lu_cancel.setMinimumHeight(34)
        self._btn_lu_cancel.setEnabled(False)
        self._btn_lu_cancel.clicked.connect(self._cancelar_landuse)
        vl.addWidget(self._btn_lu_cancel)

        self._prog_lu = QProgressBar()
        self._prog_lu.setStyleSheet(
            f'QProgressBar {{height:18px;border-radius:5px;background:#E0E0E0;}}'
            f'QProgressBar::chunk {{background:{VERDE_M};border-radius:5px;}}')
        self._prog_lu.setValue(0)
        vl.addWidget(self._prog_lu)
        vl.addStretch()
        scroll.setWidget(inner)
        spl.addWidget(scroll)

        # Panel derecho: log
        panel_log = QWidget(); panel_log.setMinimumWidth(320)
        vl_log = QVBoxLayout(panel_log)
        vl_log.setContentsMargins(8, 14, 14, 14); vl_log.setSpacing(8)
        lbl_h = QLabel('Registro de descarga LANDUSE')
        lbl_h.setStyleSheet(
            f'background:{GRIS_OS};color:white;padding:9px 14px;'
            f'font-weight:bold;font-size:12px;border-radius:6px 6px 0 0;')
        vl_log.addWidget(lbl_h)
        self._txt_lu_log = QTextEdit()
        self._txt_lu_log.setReadOnly(True)
        self._txt_lu_log.setStyleSheet(
            'QTextEdit {background:#1E272E;color:#ECF0F1;'
            'font-family:Consolas,monospace;font-size:10px;'
            'border:1.5px solid #CFD8DC;border-top:none;'
            'border-radius:0 0 6px 6px;padding:8px;}')
        self._txt_lu_log.setPlaceholderText('Los mensajes apareceran aqui...')
        vl_log.addWidget(self._txt_lu_log, 1)
        btn_clear = QPushButton('Limpiar registro')
        btn_clear.setStyleSheet(_btn_secundario())
        btn_clear.setMinimumHeight(30)
        btn_clear.clicked.connect(self._txt_lu_log.clear)
        vl_log.addWidget(btn_clear)

        spl.addWidget(panel_log)
        spl.setSizes([380, 500])
        hl = QHBoxLayout(w); hl.setContentsMargins(0, 0, 0, 0); hl.addWidget(spl)
        return w

    def _on_tab_landuse_visible(self, idx):
        if idx != 4:
            return
        if not hasattr(self, '_lbl_lu_ruta'):
            return
        carpeta = self._inp_carpeta.text().strip()
        epsg    = self._get_epsg()
        if carpeta:
            lu_dir = os.path.join(carpeta, 'LANDUSE')
            self._lbl_lu_ruta.setText(f'Ruta de salida: {lu_dir}')
        else:
            self._lbl_lu_ruta.setText('Ruta: (configura la carpeta de salida en Config GEE)')
        if epsg:
            self._lbl_lu_crs.setText(f'CRS de salida: EPSG:{epsg}')
            self._lbl_lu_crs.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};padding:6px;'
                f'border-radius:5px;font-size:10px;border:1px solid {VERDE};')
        else:
            self._lbl_lu_crs.setText(
                'CRS: EPSG:4326 sin configurar - '
                'configura el EPSG en Config GEE para reproyectar.')
            self._lbl_lu_crs.setStyleSheet(
                f'background:#FFF8E1;color:#F57F17;padding:6px;'
                f'border-radius:5px;font-size:10px;border:1px solid #F57F17;')
        if self._aoi_geom and self._datos_ficha:
            area = self._datos_ficha.get('Area', '?')
            self._lbl_lu_aoi.setText(
                f'AOI definida - Area: {area} | '
                f'Bbox: [{self._datos_ficha.get("Bbox Oeste","?")} , '
                f'{self._datos_ficha.get("Bbox Sur","?")} , '
                f'{self._datos_ficha.get("Bbox Este","?")} , '
                f'{self._datos_ficha.get("Bbox Norte","?")}]')
            self._lbl_lu_aoi.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};padding:8px;'
                f'border-radius:5px;font-size:10px;')
        else:
            self._lbl_lu_aoi.setText(
                'Sin AOI definida - ve a la pestana "Seleccionar AOI"')
            self._lbl_lu_aoi.setStyleSheet(
                f'background:{ROJO_C};color:{ROJO};padding:8px;'
                f'border-radius:5px;font-size:10px;')

    def _actualizar_info_landuse(self):
        if not hasattr(self, '_cmb_lu'):
            return
        idx = self._cmb_lu.currentIndex()
        if idx < 0 or idx >= len(self._LANDUSE_SOURCES):
            return
        s      = self._LANDUSE_SOURCES[idx]
        if s.get('has_year'):
            self._lbl_lu_year.show()
            self._spn_lu_year.show()
            self._spn_lu_year.blockSignals(True)
            self._spn_lu_year.setRange(s['year_min'], s['year_max'])
            if self._spn_lu_year.value() < s['year_min'] or self._spn_lu_year.value() > s['year_max']:
                self._spn_lu_year.setValue(s.get('year', s['year_max']))
            self._spn_lu_year.blockSignals(False)
            s['year'] = self._spn_lu_year.value()
        else:
            self._lbl_lu_year.hide()
            self._spn_lu_year.hide()

        esc    = self._spn_lu_escala.value()
        esc_ef = esc if esc > 0 else s['scale']
        n_clases = len(s['tabla_swat'])
        
        desc = s['desc']
        if s.get('has_year'):
            desc += f'<br><b>Año a descargar:</b> {s["year"]}'
            
        self._lbl_lu_desc.setText(
            f'<b>Asset GEE:</b> {s["collection"]}<br>'
            f'<b>Banda:</b> {s["banda"]}  |  '
            f'<b>Res. nativa:</b> {s["scale"]} m  |  '
            f'<b>Clases SWAT:</b> {n_clases}<br>'
            f'<b>Info:</b> {desc}<br>'
            f'<b>Escala efectiva:</b> {esc_ef} m' +
            (' <i>(nativa)</i>' if esc == 0 else ''))
        self._lbl_lu_esc_info.setText(
            f'0 = resolucion nativa ({s["scale"]} m). Efectiva: {esc_ef} m.')

    def _ejecutar_landuse(self):
        from .Script.landuse_descargar_landuse import LanduseWorker
        if not self._aoi_geom:
            QMessageBox.warning(self, 'geeSWAT',
                'Define primero el area de interes en "Seleccionar AOI".')
            self._tabs.setCurrentIndex(1)
            return
        carpeta = self._inp_carpeta.text().strip()
        if not carpeta:
            QMessageBox.warning(self, 'geeSWAT',
                'Configura la carpeta de salida en "Config GEE".')
            self._tabs.setCurrentIndex(0)
            return
        proyecto = self._inp_project.text().strip()
        idx_src  = self._cmb_lu.currentIndex()
        src      = self._LANDUSE_SOURCES[idx_src]
        if src.get('has_year'):
            src['year'] = self._spn_lu_year.value()
        esc      = self._spn_lu_escala.value()
        esc_ef   = esc if esc > 0 else src['scale']
        epsg     = self._get_epsg() or 4326
        # Bbox WGS84
        bbox = self._datos_ficha.get('_bbox_wgs84')
        if not bbox:
            from qgis.core import (QgsGeometry, QgsCoordinateReferenceSystem,
                                   QgsCoordinateTransform)
            geom  = QgsGeometry.fromWkt(self._aoi_geom)
            crs   = QgsCoordinateReferenceSystem(f'EPSG:{self._aoi_crs_epsg}')
            crs84 = QgsCoordinateReferenceSystem('EPSG:4326')
            if crs != crs84:
                geom.transform(QgsCoordinateTransform(crs, crs84, QgsProject.instance()))
            bb   = geom.boundingBox()
            bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
        lon_min, lat_min, lon_max, lat_max = bbox
        lu_dir = os.path.join(carpeta, 'LANDUSE')
        os.makedirs(lu_dir, exist_ok=True)
        nombre_proy = self._inp_nombre_proy.text().strip()
        ts      = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        id_str  = f'{src["id"]}_{src["year"]}' if src.get('has_year') else src['id']
        fname   = f'{nombre_proy}_{id_str}_{ts}' if nombre_proy else f'{id_str}_{ts}'
        out_tif = os.path.join(lu_dir, fname + '.tif')
        out_csv = os.path.join(lu_dir, fname + '_Landuse_lookup.csv')
        self._btn_lu_desc.setEnabled(False)
        self._btn_lu_cancel.setEnabled(True)
        self._prog_lu.setValue(0)
        self._txt_lu_log.clear()
        self._lu_log(f'Fuente          : {src["label"]}')
        self._lu_log(f'Asset GEE       : {src["collection"]}')
        if src.get('has_year'):
            self._lu_log(f'Año             : {src["year"]}')
        self._lu_log(f'Banda           : {src["banda"]}')
        self._lu_log(f'Escala efectiva : {esc_ef} m')
        self._lu_log(f'Proyeccion      : EPSG:{epsg}')
        self._lu_log(f'Bbox WGS84      : [{lon_min:.5f}, {lat_min:.5f}, {lon_max:.5f}, {lat_max:.5f}]')
        self._lu_log(f'Raster salida   : {out_tif}')
        self._lu_log(f'CSV salida      : {out_csv}')
        self._lu_log('-' * 50)
        self._landuse_worker = LanduseWorker(
            src, lon_min, lat_min, lon_max, lat_max,
            esc_ef, epsg, out_tif, out_csv, proyecto)
        self._landuse_worker.log.connect(self._lu_log)
        self._landuse_worker.progreso.connect(self._prog_lu.setValue)
        self._landuse_worker.ok.connect(self._lu_completado)
        self._landuse_worker.error.connect(self._lu_error)
        self._landuse_worker.finished.connect(self._lu_worker_terminado)
        self._landuse_worker.start()

    def _cancelar_landuse(self):
        if self._landuse_worker and self._landuse_worker.isRunning():
            self._landuse_worker.cancelar()
            self._lu_log('Cancelando...')

    def _lu_log(self, msg):
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._txt_lu_log.append(
            f'<span style="color:#95A5A6">[{ts}]</span> {msg}')
        sb = self._txt_lu_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _lu_completado(self, ruta_tif, ruta_csv, via_drive):
        if via_drive:
            self._lu_log('Exportacion a Google Drive completada.')
            QMessageBox.information(self, 'geeSWAT - LANDUSE Completado',
                'El uso del suelo se exporto a Google Drive.\n'
                'Descargalo desde drive.google.com')
        else:
            self._lu_log(f'Raster guardado: {ruta_tif}')
            if ruta_csv:
                self._lu_log(f'CSV guardado   : {ruta_csv}')
            try:
                import re
                if "MAPBIOMAS_" in ruta_tif:
                    match = re.search(r'MAPBIOMAS_([A-Z]+)_(\d{4})', ruta_tif)
                    if match:
                        country = match.group(1).capitalize()
                        year_str = match.group(2)
                        nombre_capa = f"MAPBIOMAS Cobertura {country} ({year_str})"
                    else:
                        nombre_capa = "MAPBIOMAS Cobertura (30 m)"
                elif "ESRI_10M" in ruta_tif:
                    match = re.search(r'ESRI_10M_(\d{4})', ruta_tif)
                    year_str = match.group(1) if match else "2024"
                    nombre_capa = f"ESRI Global LULC 10m ({year_str})"
                elif "MODIS_MCD12Q1" in ruta_tif:
                    match = re.search(r'MODIS_MCD12Q1_(\d{4})', ruta_tif)
                    year_str = match.group(1) if match else "2019"
                    nombre_capa = f"MODIS MCD12Q1 LC_Type5 ({year_str})"
                elif "COPERNICUS" in ruta_tif:
                    match = re.search(r'COPERNICUS_(\d{4})', ruta_tif)
                    year_str = match.group(1) if match else "2019"
                    nombre_capa = f"Copernicus Global Land Cover ({year_str})"
                elif "GLOBCOVER_2009" in ruta_tif:
                    nombre_capa = "ESA GlobCover 2009 (300 m)"
                elif "GLC_FCS30D" in ruta_tif:
                    match = re.search(r'GLC_FCS30D_(\d{4})', ruta_tif)
                    year_str = match.group(1) if match else "2022"
                    nombre_capa = f"GLC_FCS30D Global Land Cover ({year_str})"
                else:
                    src = self._LANDUSE_SOURCES[self._cmb_lu.currentIndex()]
                    nombre_capa = f'LANDUSE - {src["id"]}'
                    
                rlayer = QgsRasterLayer(ruta_tif, nombre_capa)
                if rlayer.isValid():
                    if "MAPBIOMAS_" in ruta_tif:
                        self._aplicar_simbologia_mapbiomas(rlayer)
                    elif "ESRI_10M" in ruta_tif:
                        self._aplicar_simbologia_esri(rlayer)
                    elif "MODIS_MCD12Q1" in ruta_tif:
                        self._aplicar_simbologia_modis(rlayer)
                    elif "COPERNICUS" in ruta_tif:
                        self._aplicar_simbologia_copernicus(rlayer)
                    elif "GLOBCOVER_2009" in ruta_tif:
                        self._aplicar_simbologia_globcover(rlayer)
                    elif "GLC_FCS30D" in ruta_tif:
                        self._aplicar_simbologia_glcfcs30d(rlayer)
                        
                    QgsProject.instance().addMapLayer(rlayer)
                    self._lu_log('Raster cargado en QGIS.')
                    
                    import os
                    out_dir = os.path.dirname(ruta_tif)
                    file_prefix = os.path.splitext(os.path.basename(ruta_tif))[0] + "_vector"
                    
                    if hasattr(self, '_chk_lu_vector') and self._chk_lu_vector.isChecked():
                        self._generar_vector_desde_raster(rlayer, out_dir, file_prefix, self._lu_log)
                    else:
                        self._lu_log('Generación de vector omitida (Checkbox desmarcado).')

            except Exception as ex:
                self._lu_log(f'No se pudo cargar en QGIS: {ex}')
            QMessageBox.information(self, 'geeSWAT - LANDUSE Completado',
                f'Raster de uso del suelo:\n  {ruta_tif}\n\n'
                f'Tabla Landuse_lookup.csv:\n  {ruta_csv or "(no generada)"}')

    def _aplicar_simbologia_glcfcs30d(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor

        clases = {
            10: ('Cultivo de secano', '#ffff64'),
            11: ('Cultivo herbáceo', '#ffff64'),
            12: ('Cultivo con cobertura arbórea o arbustiva', '#ffff00'),
            20: ('Cultivo irrigado', '#aaf0f0'),
            51: ('Bosque latifoliado perenne abierto', '#4c7300'),
            52: ('Bosque latifoliado perenne cerrado', '#006400'),
            61: ('Bosque latifoliado caducifolio abierto', '#a8c800'),
            62: ('Bosque latifoliado caducifolio cerrado', '#00a000'),
            71: ('Bosque aciculifoliado perenne abierto', '#005000'),
            72: ('Bosque aciculifoliado perenne cerrado', '#003c00'),
            81: ('Bosque aciculifoliado caducifolio abierto', '#286400'),
            82: ('Bosque aciculifoliado caducifolio cerrado', '#285000'),
            91: ('Bosque mixto abierto', '#a0b432'),
            92: ('Bosque mixto cerrado', '#788200'),
            120: ('Matorral', '#966400'),
            121: ('Matorral perenne', '#964b00'),
            122: ('Matorral caducifolio', '#966400'),
            130: ('Pastizal', '#ffb432'),
            140: ('Líquenes y musgos', '#ffdcd2'),
            150: ('Vegetación dispersa', '#ffebaf'),
            152: ('Matorral disperso', '#ffd278'),
            153: ('Vegetación herbácea dispersa', '#ffebaf'),
            181: ('Pantano boscoso', '#00a884'),
            182: ('Pantano herbáceo', '#73ffdf'),
            183: ('Llanura inundable', '#9ebb3b'),
            184: ('Área salina', '#828282'),
            185: ('Manglar', '#f57ab6'),
            186: ('Marisma salina', '#66cdab'),
            187: ('Planicie de marea', '#444f89'),
            190: ('Superficies impermeables', '#c31400'),
            200: ('Áreas desnudas', '#fff5d7'),
            201: ('Áreas desnudas consolidadas', '#dcdcdc'),
            202: ('Áreas desnudas no consolidadas', '#fff5d7'),
            210: ('Cuerpo de agua', '#0046c8'),
            220: ('Hielo y nieve permanente', '#ffffff')
        }

        classes = []
        for val, (name, hex_color) in sorted(clases.items()):
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _aplicar_simbologia_mapbiomas(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor

        clases = {
            0: ('No Data', '#ffffff'),
            1: ('Formación boscosa', '#1f8d49'),
            3: ('Bosque', '#1f8d49'),
            4: ('Bosque seco', '#7dc975'),
            5: ('Manglar', '#04381d'),
            6: ('Bosque inundable', '#026975'),
            9: ('Plantación forestal', '#7a5900'),
            10: ('Formación natural no boscosa', '#d6bc74'),
            11: ('Zona pantanosa o pastizal inundable', '#519799'),
            12: ('Pastizal / herbazal', '#d6bc74'),
            13: ('Otra formación no boscosa', '#d89f5c'),
            14: ('Área agropecuaria', '#ffefc3'),
            15: ('Pasto', '#edde8e'),
            18: ('Agricultura', '#e974ed'),
            21: ('Mosaico agropecuario', '#ffefc3'),
            22: ('Área sin vegetación', '#d4271e'),
            23: ('Playa, duna y arena', '#ffa07a'),
            24: ('Infraestructura urbana', '#d4271e'),
            25: ('Otra área sin vegetación', '#db4d4f'),
            26: ('Cuerpo de agua', '#2532e4'),
            27: ('No observado', '#ffffff'),
            29: ('Afloramiento rocoso', '#ffaa5f'),
            30: ('Minería', '#9c0027'),
            31: ('Acuicultura', '#091077'),
            32: ('Salina costera', '#fc8114'),
            33: ('Río, lago u océano', '#2532e4'),
            34: ('Glaciar', '#93dfe6'),
            35: ('Palma aceitera', '#9065d0'),
            36: ('Cultivo perenne', '#d082de'),
            39: ('Soja', '#f5b3c8'),
            40: ('Arroz', '#c71585'),
            41: ('Otros cultivos temporales', '#e787f8'),
            46: ('Café', '#cca0d4'),
            47: ('Citrus', '#d082de'),
            48: ('Otros cultivos perennes', '#d082de'),
            49: ('Vegetación boscosa sobre arena', '#02d659'),
            50: ('Vegetación xerófila', '#ad975a'),
            59: ('Bosque primario', '#1f8d49'),
            60: ('Bosque secundario', '#7dc975'),
            61: ('Salar', '#f5d5d5'),
            62: ('Algodón', '#db7093'),
            63: ('Estepa', '#d6bc74'),
            66: ('Matorral', '#a89358'),
            67: ('Bosque achaparrado', '#7dc975'),
            68: ('Otra área natural sin vegetación', '#e97a7a'),
            70: ('Loma costera', '#be9e00'),
            72: ('Otros cultivos', '#910046'),
            74: ('Plátano y banano', '#cca0d4'),
            75: ('Planta fotovoltaica', '#d4271e'),
            79: ('Plantación de pinos', '#7a5900'),
            80: ('Plantación de eucaliptus', '#7a5900'),
            81: ('Herbazal andino', '#d6bc74'),
            82: ('Herbazal inundable', '#519799'),
            83: ('Otros usos forestales', '#7a5900')
        }

        classes = []
        for val, (name, hex_color) in sorted(clases.items()):
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _aplicar_simbologia_esri(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor

        clases = {
            1: ('Agua', '#1A5BAB'),
            2: ('Árboles', '#358221'),
            4: ('Vegetación inundada', '#87D19E'),
            5: ('Cultivos', '#FFDB5C'),
            7: ('Área construida', '#ED022A'),
            8: ('Suelo desnudo', '#EDE9E4'),
            9: ('Nieve / Hielo', '#F2FAFF'),
            10: ('Nubes', '#C8C8C8'),
            11: ('Pastizales', '#C6AD8D')
        }

        classes = []
        for val, (name, hex_color) in sorted(clases.items()):
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _aplicar_simbologia_modis(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor

        clases = {
            0: ("Cuerpos de agua", "#1C0DFF"),
            1: ("Árboles de hoja acicular perenne", "#05450A"),
            2: ("Árboles de hoja perenne y hoja ancha", "#086A10"),
            3: ("Árboles de hoja caduca aciculares", "#54A708"),
            4: ("Árboles de hoja ancha caducifolios", "#78D203"),
            5: ("Arbustos", "#DCD159"),
            6: ("Herbáceos no cultivados", "#B6FF05"),
            7: ("Tierras de cultivo de cereales", "#DADE48"),
            8: ("Tierras de cultivo de hoja ancha", "#C24F44"),
            9: ("Terrenos urbanos y edificados", "#A5A5A5"),
            10: ("Nieve y hielo permanentes", "#69FFF8"),
            11: ("Tierras sin vegetación", "#F9FFA4"),
            255: ("Sin datos (nodata)", "#000000")
        }

        classes = []
        for val, (name, hex_color) in sorted(clases.items()):
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _aplicar_simbologia_copernicus(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor

        clases = {
            0: ("Desconocido", "#282828"),
            20: ("Arbustos", "#ffbb22"),
            30: ("Vegetación herbácea", "#ffff4c"),
            40: ("Agricultura", "#f096ff"),
            50: ("Zona urbana", "#fa0000"),
            60: ("Vegetación escasa", "#b4b4b4"),
            70: ("Nieve y hielo", "#f0f0f0"),
            80: ("Cuerpos de agua", "#0032c8"),
            90: ("Humedal herbáceo", "#0096a0"),
            100: ("Musgo y líquenes", "#fae6a0"),
            111: ("Bosque cerrado acicular perenne", "#58481f"),
            112: ("Bosque cerrado hoja ancha perenne", "#009900"),
            113: ("Bosque cerrado acicular caducifolio", "#70663e"),
            114: ("Bosque cerrado hoja ancha caducifolio", "#00cc00"),
            115: ("Bosque cerrado mixto", "#4e751f"),
            116: ("Bosque cerrado no coincide", "#007800"),
            121: ("Bosque abierto acicular perenne", "#666000"),
            122: ("Bosque abierto hoja ancha perenne", "#8db400"),
            123: ("Bosque abierto acicular caducifolio", "#8d7400"),
            124: ("Bosque abierto hoja ancha caducifolio", "#a0dc00"),
            125: ("Bosque abierto mixto", "#929900"),
            126: ("Bosque abierto no coincide", "#648c00"),
            200: ("Océanos y mares", "#000080")
        }

        classes = []
        for val, (name, hex_color) in sorted(clases.items()):
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _aplicar_simbologia_globcover(self, layer):
        from qgis.core import QgsPalettedRasterRenderer
        from qgis.PyQt.QtGui import QColor

        clases = {
            11: ("Tierras de cultivo irrigadas o posteriores a inundaciones", "#aaefef"),
            14: ("Tierras de cultivo de secano", "#ffff63"),
            20: ("Mosaico de tierras de cultivo (50-70%) / vegetación (pastizales, matorrales, bosques) (20-50%)", "#dcef63"),
            30: ("Mosaico de vegetación (pastizales, matorrales, bosques) (50-70%) / tierras de cultivo (20-50%)", "#cdcd64"),
            40: ("Bosque de hoja ancha perenne y/o semideciduo cerrado a abierto (>15%) (>5 m)", "#006300"),
            50: ("Bosque de hoja ancha caducifolio cerrado (>40%) (>5 m)", "#009f00"),
            60: ("Bosque de hoja ancha caducifolio abierto (15-40%) (>5 m)", "#aac700"),
            70: ("Bosque de coníferas perenne cerrado (>40%) (>5 m)", "#003b00"),
            90: ("Bosque de coníferas caducifolio o perenne abierto (15-40%) (>5 m)", "#286300"),
            100: ("Bosque mixto de hoja ancha y coníferas cerrado a abierto (>15%) (>5 m)", "#788300"),
            110: ("Mosaico de bosque-matorral (50-70%) / pastizal (20-50%)", "#8d9f00"),
            120: ("Mosaico de pastizal (50-70%) / bosque-matorral (20-50%)", "#bd9500"),
            130: ("Matorral cerrado a abierto (>15%) (<5 m)", "#956300"),
            140: ("Pastizal cerrado a abierto (>15%)", "#ffb431"),
            150: ("Vegetación dispersa (>15%) (vegetación leñosa, arbustos, pastizales)", "#ffebae"),
            160: ("Bosque de hoja ancha cerrado (>40%) inundado con regularidad (agua dulce)", "#00785a"),
            170: ("Bosque de hoja ancha semideciduo y/o perenne cerrado (>40%) inundado con regularidad (agua salina)", "#009578"),
            180: ("Vegetación cerrada a abierta (>15%) (pastizales, matorrales, vegetación leñosa) en suelo inundado o anegado con regularidad (agua dulce, salobre o salina)", "#00dc83"),
            190: ("Superficies artificiales y áreas asociadas (áreas urbanas >50%) GLOBCOVER 2009", "#c31300"),
            200: ("Áreas descubiertas", "#fff5d6"),
            210: ("Cuerpos de agua", "#0046c7"),
            220: ("Nieve y hielo permanentes", "#ffffff"),
            230: ("Sin clasificar", "#743411")
        }

        classes = []
        for val, (name, hex_color) in sorted(clases.items()):
            classes.append(QgsPalettedRasterRenderer.Class(val, QColor(hex_color), f"{val}. {name}"))
            
        renderer = QgsPalettedRasterRenderer(layer.dataProvider(), 1, classes)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

    def _generar_vector_desde_raster(self, rlayer, output_dir, file_prefix, log_func, extra_fields_dict=None):
        """Convierte el raster a vector, agrega campos NAME y area_ha, genera simbologia y lo guarda."""
        from qgis.core import (QgsVectorLayer, QgsField, QgsPalettedRasterRenderer, 
                               QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsSymbol)
        from qgis.PyQt.QtCore import QVariant, Qt
        import processing
        import os

        classes_dict = {}
        renderer = rlayer.renderer()
        if isinstance(renderer, QgsPalettedRasterRenderer):
            for cls in renderer.classes():
                name_parts = cls.label.split('.', 1)
                clean_name = name_parts[1].strip() if len(name_parts) > 1 else cls.label
                classes_dict[cls.value] = {'color': cls.color, 'name': clean_name}

        vector_path = os.path.join(output_dir, f"{file_prefix}.gpkg")

        log_func(f'Convirtiendo a vector (puede tardar en áreas grandes)...')
        params = {
            'INPUT': rlayer,
            'BAND': 1,
            'FIELD': 'id',
            'EIGHT_CONNECTEDNESS': False,
            'EXTRA': '',
            'OUTPUT': vector_path
        }
        try:
            processing.run("gdal:polygonize", params)
            log_func(f'Vector generado: {vector_path}')
        except Exception as ex:
            log_func(f"Error en polygonize: {ex}")
            return

        vlayer = QgsVectorLayer(vector_path, file_prefix, "ogr")
        if not vlayer.isValid():
            log_func('Error al cargar vector generado.')
            return

        vlayer.startEditing()
        pr = vlayer.dataProvider()
        
        campos_a_agregar = [QgsField("NAME", QVariant.String, len=255),
                            QgsField("area_ha", QVariant.Double, len=20, prec=4)]
                            
        claves_extra = []
        if extra_fields_dict:
            first_key = list(extra_fields_dict.keys())[0]
            if 'extra' in extra_fields_dict[first_key]:
                for k in extra_fields_dict[first_key]['extra'].keys():
                    campos_a_agregar.append(QgsField(str(k), QVariant.String, len=255))
                    claves_extra.append(str(k))
                    
        pr.addAttributes(campos_a_agregar)
        vlayer.updateFields()
        
        idx_id = vlayer.fields().lookupField('id')
        idx_name = vlayer.fields().lookupField('NAME')
        idx_area = vlayer.fields().lookupField('area_ha')
        
        idxs_extra = {k: vlayer.fields().lookupField(k) for k in claves_extra}

        for feat in vlayer.getFeatures():
            geom = feat.geometry()
            area_ha = geom.area() / 10000.0 if geom else 0.0
            
            val = feat.attribute(idx_id)
            name = "Desconocido"
            if val in classes_dict:
                name = classes_dict[val]['name']
            
            vlayer.changeAttributeValue(feat.id(), idx_name, name)
            vlayer.changeAttributeValue(feat.id(), idx_area, area_ha)
            
            if extra_fields_dict and val in extra_fields_dict:
                extra_data = extra_fields_dict[val].get('extra', {})
                for k in claves_extra:
                    vlayer.changeAttributeValue(feat.id(), idxs_extra[k], str(extra_data.get(k, "")))
            
        vlayer.commitChanges()
        log_func('Campos NAME y area_ha calculados en el vector.')

        if classes_dict:
            categories = []
            for val, info in classes_dict.items():
                sym = QgsSymbol.defaultSymbol(vlayer.geometryType())
                sym.setColor(info['color'])
                if sym.symbolLayer(0):
                    sym.symbolLayer(0).setStrokeStyle(Qt.NoPen)
                cat = QgsRendererCategory(info['name'], sym, str(info['name']))
                categories.append(cat)
                
            renderer_cat = QgsCategorizedSymbolRenderer('NAME', categories)
            vlayer.setRenderer(renderer_cat)
            
            qml_path = os.path.join(output_dir, f"{file_prefix}.qml")
            vlayer.saveNamedStyle(qml_path)
            log_func(f'Simbología vectorial guardada en: {qml_path}')
            
        from qgis.core import QgsProject
        QgsProject.instance().addMapLayer(vlayer)
        log_func('Vector de uso de suelo cargado en QGIS.')

    def _lu_error(self, msg):
        self._lu_log(f'ERROR: {msg}')
        QMessageBox.critical(self, 'geeSWAT - Error LANDUSE', msg)

    def _lu_worker_terminado(self):
        self._btn_lu_desc.setEnabled(True)
        self._btn_lu_cancel.setEnabled(False)

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA CN - CURVA NÚMERO
    # ══════════════════════════════════════════════════════════════════════════

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA X — CLIMÁTICOS
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_climaticos(self):
        from .Script.climaticos_descargar import CLIMATIC_PRODUCTS
        self._CLIMATIC_PRODUCTS = CLIMATIC_PRODUCTS

        w = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        cont = QWidget()
        l = QVBoxLayout(cont)
        l.setContentsMargins(20, 20, 20, 20)
        l.setSpacing(15)

        # 1. Estaciones Virtuales
        g_est = _grupo('Estaciones Virtuales', AZUL_M)
        g_est_l = QVBoxLayout(g_est)

        self._cmb_clim_estaciones = QComboBox()
        self._cmb_clim_estaciones.addItem("Generar Malla (GEE)", userData="malla")
        self._cmb_clim_estaciones.addItem("Seleccionar Capa Vectorial de Puntos...", userData="capa")
        
        self._cmb_clim_layer = QgsMapLayerComboBox()
        self._cmb_clim_layer.setFilters(QgsMapLayerProxyModel.PointLayer)
        self._cmb_clim_layer.hide()

        self._lbl_clim_grid = QLabel('Espaciamiento de grilla (m):')
        self._spn_clim_grid = QSpinBox()
        self._spn_clim_grid.setRange(1000, 200000)
        self._spn_clim_grid.setValue(50000)
        self._spn_clim_grid.setSingleStep(1000)
        
        def _on_est_type_changed():
            if self._cmb_clim_estaciones.currentData() == "malla":
                self._cmb_clim_layer.hide()
                self._lbl_clim_grid.show()
                self._spn_clim_grid.show()
            else:
                self._cmb_clim_layer.show()
                self._lbl_clim_grid.hide()
                self._spn_clim_grid.hide()

        self._cmb_clim_estaciones.currentIndexChanged.connect(_on_est_type_changed)

        h_est = QHBoxLayout()
        h_est.addWidget(QLabel('Fuente:'))
        h_est.addWidget(self._cmb_clim_estaciones)
        g_est_l.addLayout(h_est)
        g_est_l.addWidget(self._cmb_clim_layer)
        
        h_grid = QHBoxLayout()
        h_grid.addWidget(self._lbl_clim_grid)
        h_grid.addWidget(self._spn_clim_grid)
        g_est_l.addLayout(h_grid)
        l.addWidget(g_est)

        # 2. Producto Climático
        g_prod = _grupo('Producto Climático', AZUL_M)
        g_prod_l = QVBoxLayout(g_prod)

        self._cmb_clim_prod = QComboBox()
        for p in self._CLIMATIC_PRODUCTS:
            self._cmb_clim_prod.addItem(p["name"], userData=p["id"])
            
        h_prod = QHBoxLayout()
        h_prod.addWidget(QLabel('Colección:'))
        h_prod.addWidget(self._cmb_clim_prod)
        g_prod_l.addLayout(h_prod)
        
        self._lbl_clim_info = QLabel('')
        self._lbl_clim_info.setWordWrap(True)
        self._lbl_clim_info.setStyleSheet('color:#37474F;font-size:11px;font-style:italic;padding:5px;background:#E3F2FD;border-radius:3px;')
        g_prod_l.addWidget(self._lbl_clim_info)

        def _update_clim_info():
            idx = self._cmb_clim_prod.currentIndex()
            if idx >= 0:
                p = self._CLIMATIC_PRODUCTS[idx]
                info = f"<b>Variables:</b> {p.get('desc', '')}<br><b>Disponibilidad:</b> {p.get('date_range', '')}<br><b>Resolución Espacial:</b> {p.get('res', 'No especificada')}"
                self._lbl_clim_info.setText(info)
        
        self._cmb_clim_prod.currentIndexChanged.connect(_update_clim_info)
        _update_clim_info()
        
        h_dates = QHBoxLayout()
        self._dt_inicio = QDateEdit()
        self._dt_inicio.setDisplayFormat("yyyy-MM-dd")
        self._dt_inicio.setDate(QDate(2019, 1, 1))
        self._dt_inicio.setCalendarPopup(True)
        
        self._dt_fin = QDateEdit()
        self._dt_fin.setDisplayFormat("yyyy-MM-dd")
        self._dt_fin.setDate(QDate(2026, 1, 1))
        self._dt_fin.setCalendarPopup(True)
        
        h_dates.addWidget(QLabel('Inicio:'))
        h_dates.addWidget(self._dt_inicio)
        h_dates.addWidget(QLabel('Fin:'))
        h_dates.addWidget(self._dt_fin)
        g_prod_l.addLayout(h_dates)
        l.addWidget(g_prod)

        # 3. Descarga
        self._btn_clim_desc = QPushButton('Descargar y Exportar a SWAT')
        self._btn_clim_desc.setStyleSheet(f'background:{VERDE};color:white;font-weight:bold;padding:10px;border-radius:4px;')
        self._btn_clim_cancel = QPushButton('Cancelar')
        self._btn_clim_cancel.setEnabled(False)

        h_btn = QHBoxLayout()
        h_btn.addWidget(self._btn_clim_desc)
        h_btn.addWidget(self._btn_clim_cancel)
        l.addLayout(h_btn)

        self._prog_clim = QProgressBar(); self._prog_clim.setValue(0); self._prog_clim.hide()
        l.addWidget(self._prog_clim)

        l.addWidget(QLabel('Registro de descarga Climáticos:'))
        self._txt_clim_log = QTextEdit(); self._txt_clim_log.setReadOnly(True)
        self._txt_clim_log.setStyleSheet('background:#2B2B2B;color:#A9B7C6;font-family:Consolas;font-size:10px;')
        l.addWidget(self._txt_clim_log)

        l.addStretch()
        cont.setLayout(l)
        scroll.setWidget(cont)
        w.setLayout(QVBoxLayout())
        w.layout().setContentsMargins(0,0,0,0)
        w.layout().addWidget(scroll)

        self._btn_clim_desc.clicked.connect(self._ejecutar_climaticos)
        self._btn_clim_cancel.clicked.connect(self._cancelar_climaticos)

        return w

    def _ejecutar_climaticos(self):
        from .Script.climaticos_descargar import ClimaticosWorker
        
        if not self._aoi_geom:
            QMessageBox.warning(self, 'geeSWAT', 'Primero debe definir el Área de Interés (AOI).')
            return
        
        carpeta = self._inp_carpeta.text().strip()
        if not carpeta or not os.path.exists(carpeta):
            QMessageBox.warning(self, 'geeSWAT', 'Carpeta de salida inválida en Configuración.')
            return
            
        product_id = self._cmb_clim_prod.currentData()
        date_start = self._dt_inicio.date().toString("yyyy-MM-dd")
        date_end = self._dt_fin.date().toString("yyyy-MM-dd")
        
        estaciones_layer = None
        grid_spacing = None
        
        if self._cmb_clim_estaciones.currentData() == "malla":
            grid_spacing = self._spn_clim_grid.value()
        else:
            estaciones_layer = self._cmb_clim_layer.currentLayer()
            if not estaciones_layer:
                QMessageBox.warning(self, 'geeSWAT', 'Debe seleccionar una capa de estaciones válida.')
                return
                
        # Calcular geom_json en WGS84
        from qgis.core import QgsGeometry, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
        geom  = QgsGeometry.fromWkt(self._aoi_geom)
        crs   = QgsCoordinateReferenceSystem(f'EPSG:{self._aoi_crs_epsg}')
        crs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        if crs != crs84:
            geom.transform(QgsCoordinateTransform(crs, crs84, QgsProject.instance()))
        geom_json = geom.asJson()

        clim_dir = os.path.join(carpeta, 'CLIMATICOS')
        os.makedirs(clim_dir, exist_ok=True)

        self._btn_clim_desc.setEnabled(False)
        self._btn_clim_cancel.setEnabled(True)
        self._prog_clim.show()
        self._prog_clim.setValue(0)
        self._txt_clim_log.clear()

        self._climaticos_worker = ClimaticosWorker(
            output_dir=clim_dir,
            geom_json=geom_json,
            product_id=product_id,
            date_start=date_start,
            date_end=date_end,
            estaciones_layer=estaciones_layer,
            grid_spacing=grid_spacing
        )
        self._climaticos_worker.log.connect(self._climaticos_log)
        self._climaticos_worker.progreso.connect(self._prog_clim.setValue)
        self._climaticos_worker.ok.connect(self._climaticos_completado)
        self._climaticos_worker.error.connect(self._climaticos_error)
        self._climaticos_worker.stations_generated.connect(self._climaticos_cargar_estaciones)
        self._climaticos_worker.finished.connect(self._climaticos_worker_terminado)
        self._climaticos_worker.start()

    def _cancelar_climaticos(self):
        if self._climaticos_worker and self._climaticos_worker.isRunning():
            self._climaticos_worker.cancelar()

    def _climaticos_worker_terminado(self):
        self._btn_clim_desc.setEnabled(True)
        self._btn_clim_cancel.setEnabled(False)
        self._prog_clim.hide()

    def _climaticos_log(self, msg):
        self._txt_clim_log.append(msg)
        self._txt_clim_log.verticalScrollBar().setValue(self._txt_clim_log.verticalScrollBar().maximum())

    def _climaticos_cargar_estaciones(self, shp_path):
        if os.path.exists(shp_path):
            vlayer = QgsVectorLayer(shp_path, "Estaciones Virtuales", "ogr")
            if vlayer.isValid():
                QgsProject.instance().addMapLayer(vlayer)

    def _climaticos_completado(self, folder):
        msg = f"Descarga climática y formato SWAT completado en:\n{folder}"
        QMessageBox.information(self, 'geeSWAT - CLIMÁTICOS', msg)

    def _climaticos_error(self, msg):
        QMessageBox.critical(self, 'geeSWAT - Error CLIMÁTICOS', msg)

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA X+1 — CAMBIO CLIMÁTICO
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_cambio_climatico(self):
        from .Script.cambio_climatico_descargar import MODELS, SCENARIOS
        self._CC_MODELS = MODELS
        self._CC_SCENARIOS = SCENARIOS

        w = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        cont = QWidget()
        l = QVBoxLayout(cont)
        l.setContentsMargins(20, 20, 20, 20)
        l.setSpacing(15)

        # 1. Estaciones Virtuales
        g_est = _grupo('Estaciones Virtuales', AZUL_M)
        g_est_l = QVBoxLayout(g_est)

        self._cmb_cc_estaciones = QComboBox()
        self._cmb_cc_estaciones.addItem("Generar Malla (GEE)", userData="malla")
        self._cmb_cc_estaciones.addItem("Seleccionar Capa Vectorial de Puntos...", userData="capa")
        
        self._cmb_cc_layer = QgsMapLayerComboBox()
        self._cmb_cc_layer.setFilters(QgsMapLayerProxyModel.PointLayer)
        self._cmb_cc_layer.hide()

        self._lbl_cc_grid = QLabel('Espaciamiento de grilla (m):')
        self._spn_cc_grid = QSpinBox()
        self._spn_cc_grid.setRange(1000, 200000)
        self._spn_cc_grid.setValue(50000)
        self._spn_cc_grid.setSingleStep(1000)
        
        def _on_cc_est_type_changed():
            if self._cmb_cc_estaciones.currentData() == "malla":
                self._cmb_cc_layer.hide()
                self._lbl_cc_grid.show()
                self._spn_cc_grid.show()
            else:
                self._cmb_cc_layer.show()
                self._lbl_cc_grid.hide()
                self._spn_cc_grid.hide()

        self._cmb_cc_estaciones.currentIndexChanged.connect(_on_cc_est_type_changed)

        h_est = QHBoxLayout()
        h_est.addWidget(QLabel('Fuente:'))
        h_est.addWidget(self._cmb_cc_estaciones)
        g_est_l.addLayout(h_est)
        g_est_l.addWidget(self._cmb_cc_layer)
        
        h_grid = QHBoxLayout()
        h_grid.addWidget(self._lbl_cc_grid)
        h_grid.addWidget(self._spn_cc_grid)
        g_est_l.addLayout(h_grid)
        l.addWidget(g_est)

        # 2. Producto Climático (CMIP6)
        g_prod = _grupo('NASA/GDDP-CMIP6', AZUL_M)
        g_prod_l = QVBoxLayout(g_prod)

        self._cmb_cc_model = QComboBox()
        self._cmb_cc_model.addItems(self._CC_MODELS)
        if "MIROC6" in self._CC_MODELS:
            self._cmb_cc_model.setCurrentText("MIROC6")
            
        h_model = QHBoxLayout()
        h_model.addWidget(QLabel('Modelo:'))
        h_model.addWidget(self._cmb_cc_model)
        g_prod_l.addLayout(h_model)

        self._cmb_cc_scenario = QComboBox()
        for k, v in self._CC_SCENARIOS.items():
            self._cmb_cc_scenario.addItem(k, userData=v)

        h_scenario = QHBoxLayout()
        h_scenario.addWidget(QLabel('Escenario:'))
        h_scenario.addWidget(self._cmb_cc_scenario)
        g_prod_l.addLayout(h_scenario)
        
        h_dates = QHBoxLayout()
        self._dt_cc_inicio = QDateEdit()
        self._dt_cc_inicio.setDisplayFormat("yyyy-MM-dd")
        self._dt_cc_inicio.setDate(QDate(1950, 1, 1))
        self._dt_cc_inicio.setCalendarPopup(True)
        
        self._dt_cc_fin = QDateEdit()
        self._dt_cc_fin.setDisplayFormat("yyyy-MM-dd")
        self._dt_cc_fin.setDate(QDate(2100, 12, 31))
        self._dt_cc_fin.setCalendarPopup(True)
        
        h_dates.addWidget(QLabel('Inicio:'))
        h_dates.addWidget(self._dt_cc_inicio)
        h_dates.addWidget(QLabel('Fin:'))
        h_dates.addWidget(self._dt_cc_fin)
        g_prod_l.addLayout(h_dates)
        l.addWidget(g_prod)

        # 3. Variables a descargar
        g_vars = _grupo('Variables a descargar', AZUL_M)
        g_vars_l = QVBoxLayout(g_vars)
        
        self._chk_cc_prec = QCheckBox('Precipitación')
        self._chk_cc_prec.setChecked(True)
        self._chk_cc_temp = QCheckBox('Temperatura (Max/Min)')
        self._chk_cc_temp.setChecked(True)
        self._chk_cc_hr = QCheckBox('Humedad Relativa')
        self._chk_cc_hr.setChecked(True)
        self._chk_cc_wind = QCheckBox('Velocidad del viento')
        self._chk_cc_wind.setChecked(True)
        self._chk_cc_solar = QCheckBox('Radiación solar')
        self._chk_cc_solar.setChecked(True)
        
        gl_vars = QGridLayout()
        gl_vars.addWidget(self._chk_cc_prec, 0, 0)
        gl_vars.addWidget(self._chk_cc_temp, 0, 1)
        gl_vars.addWidget(self._chk_cc_hr, 1, 0)
        gl_vars.addWidget(self._chk_cc_wind, 1, 1)
        gl_vars.addWidget(self._chk_cc_solar, 2, 0)
        g_vars_l.addLayout(gl_vars)
        l.addWidget(g_vars)

        # 4. Descarga
        self._btn_cc_desc = QPushButton('Descargar y Exportar a SWAT')
        self._btn_cc_desc.setStyleSheet(f'background:{VERDE};color:white;font-weight:bold;padding:10px;border-radius:4px;')
        self._btn_cc_cancel = QPushButton('Cancelar')
        self._btn_cc_cancel.setEnabled(False)

        h_btn = QHBoxLayout()
        h_btn.addWidget(self._btn_cc_desc)
        h_btn.addWidget(self._btn_cc_cancel)
        l.addLayout(h_btn)

        self._prog_cc = QProgressBar(); self._prog_cc.setValue(0); self._prog_cc.hide()
        l.addWidget(self._prog_cc)

        l.addWidget(QLabel('Registro de descarga Cambio Climático:'))
        self._txt_cc_log = QTextEdit(); self._txt_cc_log.setReadOnly(True)
        self._txt_cc_log.setStyleSheet('background:#2B2B2B;color:#A9B7C6;font-family:Consolas;font-size:10px;')
        l.addWidget(self._txt_cc_log)

        l.addStretch()
        cont.setLayout(l)
        scroll.setWidget(cont)
        w.setLayout(QVBoxLayout())
        w.layout().setContentsMargins(0,0,0,0)
        w.layout().addWidget(scroll)

        self._btn_cc_desc.clicked.connect(self._ejecutar_cc)
        self._btn_cc_cancel.clicked.connect(self._cancelar_cc)

        self._cc_worker = None

        return w

    def _ejecutar_cc(self):
        from .Script.cambio_climatico_descargar import CambioClimaticoWorker
        
        if not self._aoi_geom:
            QMessageBox.warning(self, 'geeSWAT', 'Primero debe definir el Área de Interés (AOI).')
            return
        
        carpeta = self._inp_carpeta.text().strip()
        if not carpeta or not os.path.exists(carpeta):
            QMessageBox.warning(self, 'geeSWAT', 'Carpeta de salida inválida en Configuración.')
            return
            
        model_name = self._cmb_cc_model.currentText()
        scenario = self._cmb_cc_scenario.currentData()
        date_start = self._dt_cc_inicio.date().toString("yyyy-MM-dd")
        date_end = self._dt_cc_fin.date().toString("yyyy-MM-dd")
        
        estaciones_layer = None
        grid_spacing = None
        
        if self._cmb_cc_estaciones.currentData() == "malla":
            grid_spacing = self._spn_cc_grid.value()
        else:
            estaciones_layer = self._cmb_cc_layer.currentLayer()
            if not estaciones_layer:
                QMessageBox.warning(self, 'geeSWAT', 'Debe seleccionar una capa de estaciones válida.')
                return
                
        from qgis.core import QgsGeometry, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
        geom  = QgsGeometry.fromWkt(self._aoi_geom)
        crs   = QgsCoordinateReferenceSystem(f'EPSG:{self._aoi_crs_epsg}')
        crs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        if crs != crs84:
            geom.transform(QgsCoordinateTransform(crs, crs84, QgsProject.instance()))
        geom_json = geom.asJson()

        cc_dir = os.path.join(carpeta, 'CAMBIO_CLIMATICO', scenario)
        os.makedirs(cc_dir, exist_ok=True)

        vars_to_write = []
        if self._chk_cc_prec.isChecked(): vars_to_write.append("Prec")
        if self._chk_cc_temp.isChecked(): vars_to_write.append("Temp")
        if self._chk_cc_hr.isChecked(): vars_to_write.append("HR")
        if self._chk_cc_wind.isChecked(): vars_to_write.append("Wind")
        if self._chk_cc_solar.isChecked(): vars_to_write.append("Solar")
        
        if not vars_to_write:
            QMessageBox.warning(self, 'geeSWAT', 'Debe seleccionar al menos una variable.')
            return

        self._btn_cc_desc.setEnabled(False)
        self._btn_cc_cancel.setEnabled(True)
        self._prog_cc.show()
        self._prog_cc.setValue(0)
        self._txt_cc_log.clear()

        self._cc_worker = CambioClimaticoWorker(
            output_dir=cc_dir,
            geom_json=geom_json,
            model_name=model_name,
            scenario=scenario,
            date_start=date_start,
            date_end=date_end,
            estaciones_layer=estaciones_layer,
            grid_spacing=grid_spacing,
            vars_to_write=vars_to_write
        )
        self._cc_worker.log.connect(self._cc_log)
        self._cc_worker.progreso.connect(self._prog_cc.setValue)
        self._cc_worker.ok.connect(self._cc_completado)
        self._cc_worker.error.connect(self._cc_error)
        self._cc_worker.stations_generated.connect(self._cc_cargar_estaciones)
        self._cc_worker.finished.connect(self._cc_worker_terminado)
        self._cc_worker.start()

    def _cancelar_cc(self):
        if self._cc_worker and self._cc_worker.isRunning():
            self._cc_worker.cancelar()

    def _cc_worker_terminado(self):
        self._btn_cc_desc.setEnabled(True)
        self._btn_cc_cancel.setEnabled(False)
        self._prog_cc.hide()

    def _cc_log(self, msg):
        self._txt_cc_log.append(msg)
        self._txt_cc_log.verticalScrollBar().setValue(self._txt_cc_log.verticalScrollBar().maximum())

    def _cc_cargar_estaciones(self, shp_path):
        if os.path.exists(shp_path):
            vlayer = QgsVectorLayer(shp_path, "Estaciones Virtuales CC", "ogr")
            if vlayer.isValid():
                QgsProject.instance().addMapLayer(vlayer)

    def _cc_completado(self, folder):
        msg = f"Descarga de Cambio Climático completada en:\n{folder}"
        QMessageBox.information(self, 'geeSWAT - CAMBIO CLIMÁTICO', msg)

    def _cc_error(self, msg):
        QMessageBox.critical(self, 'geeSWAT - Error CAMBIO CLIMÁTICO', msg)

    def _tab_cn(self):
        from .Script.cn_descargar_cn import CN_SOURCES
        self._CN_SOURCES = CN_SOURCES

        w   = QWidget()
        spl = QSplitter(Qt.Horizontal)
        spl.setHandleWidth(6)
        spl.setStyleSheet('QSplitter::handle {background:#CFD8DC;}')

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setMinimumWidth(310)
        vl = QVBoxLayout(inner)
        vl.setContentsMargins(14, 14, 8, 14); vl.setSpacing(14)

        # AOI
        g_aoi = _grupo('Area de Interes (AOI)', AZUL)
        vl_aoi = QVBoxLayout(g_aoi); vl_aoi.setSpacing(6)
        self._lbl_cn_aoi = QLabel(
            'Sin AOI definida - ve a la pestana "Seleccionar AOI"')
        self._lbl_cn_aoi.setWordWrap(True)
        self._lbl_cn_aoi.setStyleSheet(
            f'background:{ROJO_C};color:{ROJO};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_aoi.addWidget(self._lbl_cn_aoi)
        btn_ir = QPushButton('Ir a Definir AOI')
        btn_ir.setStyleSheet(_btn_secundario())
        btn_ir.setMinimumHeight(30)
        btn_ir.clicked.connect(lambda: self._tabs.setCurrentIndex(1))
        vl_aoi.addWidget(btn_ir)
        vl.addWidget(g_aoi)

        # Fuente Curva Número
        g_src = _grupo('Fuente de Curva Número (CN)', AZUL_M)
        vl_src = QVBoxLayout(g_src); vl_src.setSpacing(10)

        self._cmb_cn = QComboBox()
        self._cmb_cn.setMinimumHeight(32)
        for s in CN_SOURCES:
            self._cmb_cn.addItem(s['label'])
        self._cmb_cn.currentIndexChanged.connect(self._actualizar_info_cn)
        vl_src.addWidget(self._cmb_cn)

        self._lbl_cn_desc = QLabel('')
        self._lbl_cn_desc.setWordWrap(True)
        self._lbl_cn_desc.setStyleSheet(
            f'background:{AZUL_C};color:{GRIS_OS};padding:8px;'
            f'border-radius:5px;font-size:10px;')
        vl_src.addWidget(self._lbl_cn_desc)

        # Escala
        gl_s = QGridLayout(); gl_s.setSpacing(8)
        gl_s.addWidget(QLabel('Escala de salida (m):'), 0, 0)
        self._spn_cn_escala = QSpinBox()
        self._spn_cn_escala.setRange(0, 10000)
        self._spn_cn_escala.setValue(0)
        self._spn_cn_escala.setMinimumHeight(30)
        self._spn_cn_escala.setSpecialValueText('0  (resolucion nativa)')
        self._spn_cn_escala.valueChanged.connect(self._actualizar_info_cn)
        gl_s.addWidget(self._spn_cn_escala, 0, 1)
        self._lbl_cn_esc_info = QLabel('')
        self._lbl_cn_esc_info.setStyleSheet(f'color:{GRIS};font-size:10px;')
        gl_s.addWidget(self._lbl_cn_esc_info, 1, 0, 1, 2)
        vl_src.addLayout(gl_s)
        vl.addWidget(g_src)
        self._actualizar_info_cn()

        # Opciones Subcuenca (Opcional)
        g_sub = _grupo('Cálculo por Subcuenca (Opcional)', NARANJA)
        vl_sub = QVBoxLayout(g_sub); vl_sub.setSpacing(8)
        lbl_sub = QLabel(
            'Para generar la tabla CSV con el CN promedio por subcuenca, '
            'seleccione la capa vectorial local de subcuencas:')
        lbl_sub.setWordWrap(True)
        lbl_sub.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_sub.addWidget(lbl_sub)

        hl_sub = QHBoxLayout()
        self._cmb_cn_subcuencas = QComboBox()
        self._cmb_cn_subcuencas.setMinimumHeight(30)
        self._cmb_cn_subcuencas.addItem("Ninguna (Solo descargar raster)", "")
        btn_refrescar_sub = QPushButton('↻')
        btn_refrescar_sub.setFixedSize(30, 30)
        btn_refrescar_sub.setStyleSheet(_btn_secundario())
        btn_refrescar_sub.clicked.connect(self._refrescar_capas_cn)
        hl_sub.addWidget(self._cmb_cn_subcuencas, 1)
        hl_sub.addWidget(btn_refrescar_sub)
        vl_sub.addLayout(hl_sub)
        
        lbl_asset = QLabel('O introduzca un ID de Asset GEE (Ej: projects/bosquepy/...):')
        lbl_asset.setStyleSheet(f'color:{GRIS};font-size:10px;')
        vl_sub.addWidget(lbl_asset)
        self._inp_cn_asset = QLineEdit()
        self._inp_cn_asset.setPlaceholderText('Dejar en blanco si se usa capa local')
        self._inp_cn_asset.setMinimumHeight(30)
        vl_sub.addWidget(self._inp_cn_asset)
        vl.addWidget(g_sub)

        # Salida
        g_sal = _grupo('Archivo de salida', VERDE)
        vl_sal = QVBoxLayout(g_sal); vl_sal.setSpacing(8)
        self._lbl_cn_ruta = QLabel(
            'Ruta: (configura la carpeta de salida en Config GEE)')
        self._lbl_cn_ruta.setWordWrap(True)
        self._lbl_cn_ruta.setStyleSheet(
            f'color:{GRIS};font-size:10px;font-style:italic;')
        vl_sal.addWidget(self._lbl_cn_ruta)
        vl.addWidget(g_sal)

        # CRS info
        self._lbl_cn_crs = QLabel('CRS: (configura el EPSG en Config GEE)')
        self._lbl_cn_crs.setStyleSheet(
            f'background:#FFF8E1;color:#F57F17;padding:6px;'
            f'border-radius:5px;font-size:10px;border:1px solid #F57F17;')
        self._lbl_cn_crs.setWordWrap(True)
        vl.addWidget(self._lbl_cn_crs)

        # Actualizar al cambiar de pestana (idx=5 ahora)
        self._tabs.currentChanged.connect(self._on_tab_cn_visible)

        self._btn_cn_desc = QPushButton('Descargar Curva Número')
        self._btn_cn_desc.setStyleSheet(_btn_exito())
        self._btn_cn_desc.setMinimumHeight(42)
        self._btn_cn_desc.setFont(QFont('Arial', 11, QFont.Bold))
        self._btn_cn_desc.clicked.connect(self._ejecutar_cn)
        vl.addWidget(self._btn_cn_desc)

        self._btn_cn_cancel = QPushButton('Cancelar')
        self._btn_cn_cancel.setStyleSheet(_btn_secundario())
        self._btn_cn_cancel.setMinimumHeight(34)
        self._btn_cn_cancel.setEnabled(False)
        self._btn_cn_cancel.clicked.connect(self._cancelar_cn)
        vl.addWidget(self._btn_cn_cancel)

        self._prog_cn = QProgressBar()
        self._prog_cn.setStyleSheet(
            f'QProgressBar {{height:18px;border-radius:5px;background:#E0E0E0;}}'
            f'QProgressBar::chunk {{background:{VERDE_M};border-radius:5px;}}')
        self._prog_cn.setValue(0)
        vl.addWidget(self._prog_cn)
        vl.addStretch()
        scroll.setWidget(inner)
        spl.addWidget(scroll)

        # Panel derecho: log
        panel_log = QWidget(); panel_log.setMinimumWidth(320)
        vl_log = QVBoxLayout(panel_log)
        vl_log.setContentsMargins(8, 14, 14, 14); vl_log.setSpacing(8)
        lbl_h = QLabel('Registro de descarga Curva Número')
        lbl_h.setStyleSheet(
            f'background:{GRIS_OS};color:white;padding:9px 14px;'
            f'font-weight:bold;font-size:12px;border-radius:6px 6px 0 0;')
        vl_log.addWidget(lbl_h)
        self._txt_cn_log = QTextEdit()
        self._txt_cn_log.setReadOnly(True)
        self._txt_cn_log.setStyleSheet(
            'QTextEdit {background:#1E272E;color:#ECF0F1;'
            'font-family:Consolas,monospace;font-size:10px;'
            'border:1.5px solid #CFD8DC;border-top:none;'
            'border-radius:0 0 6px 6px;padding:8px;}')
        self._txt_cn_log.setPlaceholderText('Los mensajes apareceran aqui...')
        vl_log.addWidget(self._txt_cn_log, 1)
        btn_clear = QPushButton('Limpiar registro')
        btn_clear.setStyleSheet(_btn_secundario())
        btn_clear.setMinimumHeight(30)
        btn_clear.clicked.connect(self._txt_cn_log.clear)
        vl_log.addWidget(btn_clear)

        spl.addWidget(panel_log)
        spl.setSizes([380, 500])
        hl = QHBoxLayout(w); hl.setContentsMargins(0, 0, 0, 0); hl.addWidget(spl)
        return w

    def _refrescar_capas_cn(self):
        if not hasattr(self, '_cmb_cn_subcuencas'):
            return
        capas = QgsProject.instance().mapLayers().values()
        v_capas = [c for c in capas if isinstance(c, QgsVectorLayer) and c.geometryType() == QgsWkbTypes.PolygonGeometry]
        curr = self._cmb_cn_subcuencas.currentData()
        self._cmb_cn_subcuencas.blockSignals(True)
        self._cmb_cn_subcuencas.clear()
        self._cmb_cn_subcuencas.addItem("Ninguna (Solo descargar raster)", "")
        idx_sel = 0
        for i, c in enumerate(v_capas):
            self._cmb_cn_subcuencas.addItem(c.name(), c.id())
            if curr and c.id() == curr:
                idx_sel = i + 1
        self._cmb_cn_subcuencas.setCurrentIndex(idx_sel)
        self._cmb_cn_subcuencas.blockSignals(False)

    def _on_tab_cn_visible(self, idx):
        if idx != 5:
            return
        if not hasattr(self, '_lbl_cn_ruta'):
            return
        self._refrescar_capas_cn()
        carpeta = self._inp_carpeta.text().strip()
        epsg    = self._get_epsg()
        if carpeta:
            cn_dir = os.path.join(carpeta, 'CN')
            self._lbl_cn_ruta.setText(f'Ruta de salida: {cn_dir}')
        else:
            self._lbl_cn_ruta.setText('Ruta: (configura la carpeta de salida en Config GEE)')
        if epsg:
            self._lbl_cn_crs.setText(f'CRS de salida: EPSG:{epsg}')
            self._lbl_cn_crs.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};padding:6px;'
                f'border-radius:5px;font-size:10px;border:1px solid {VERDE};')
        else:
            self._lbl_cn_crs.setText(
                'CRS: EPSG:4326 sin configurar - '
                'configura el EPSG en Config GEE para reproyectar.')
            self._lbl_cn_crs.setStyleSheet(
                f'background:#FFF8E1;color:#F57F17;padding:6px;'
                f'border-radius:5px;font-size:10px;border:1px solid #F57F17;')
        if self._aoi_geom and self._datos_ficha:
            area = self._datos_ficha.get('Area', '?')
            self._lbl_cn_aoi.setText(
                f'AOI definida - Area: {area} | '
                f'Bbox: [{self._datos_ficha.get("Bbox Oeste","?")} , '
                f'{self._datos_ficha.get("Bbox Sur","?")} , '
                f'{self._datos_ficha.get("Bbox Este","?")} , '
                f'{self._datos_ficha.get("Bbox Norte","?")}]')
            self._lbl_cn_aoi.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};padding:8px;'
                f'border-radius:5px;font-size:10px;')
        else:
            self._lbl_cn_aoi.setText(
                'Sin AOI definida - ve a la pestana "Seleccionar AOI"')
            self._lbl_cn_aoi.setStyleSheet(
                f'background:{ROJO_C};color:{ROJO};padding:8px;'
                f'border-radius:5px;font-size:10px;')

    def _actualizar_info_cn(self):
        if not hasattr(self, '_cmb_cn'):
            return
        idx = self._cmb_cn.currentIndex()
        if idx < 0 or idx >= len(self._CN_SOURCES):
            return
        s      = self._CN_SOURCES[idx]
        esc    = self._spn_cn_escala.value()
        esc_ef = esc if esc > 0 else s['scale']
        self._lbl_cn_desc.setText(
            f'<b>Asset GEE:</b> {s["collection"]}<br>'
            f'<b>Banda:</b> {s["banda"]}  |  '
            f'<b>Res. nativa:</b> {s["scale"]} m<br>'
            f'<b>Info:</b> {s["desc"]}<br>'
            f'<b>Escala efectiva:</b> {esc_ef} m' +
            (' <i>(nativa)</i>' if esc == 0 else ''))
        self._lbl_cn_esc_info.setText(
            f'0 = resolucion nativa ({s["scale"]} m). Efectiva: {esc_ef} m.')

    def _ejecutar_cn(self):
        from .Script.cn_descargar_cn import CNWorker
        import json
        if not self._aoi_geom:
            QMessageBox.warning(self, 'geeSWAT',
                'Define primero el area de interes en "Seleccionar AOI".')
            self._tabs.setCurrentIndex(1)
            return
        carpeta = self._inp_carpeta.text().strip()
        if not carpeta:
            QMessageBox.warning(self, 'geeSWAT',
                'Configura la carpeta de salida en "Config GEE".')
            self._tabs.setCurrentIndex(0)
            return
        proyecto = self._inp_project.text().strip()
        idx_src  = self._cmb_cn.currentIndex()
        src      = self._CN_SOURCES[idx_src]
        esc      = self._spn_cn_escala.value()
        esc_ef   = esc if esc > 0 else src['scale']
        epsg     = self._get_epsg() or 4326
        
        # Opciones Subcuenca
        subcuencas_data = None
        asset_id = self._inp_cn_asset.text().strip()
        if asset_id:
            subcuencas_data = {"type": "asset", "id": asset_id}
        else:
            layer_id = self._cmb_cn_subcuencas.currentData()
            if layer_id:
                layer = QgsProject.instance().mapLayer(layer_id)
                if layer:
                    try:
                        self._cn_log('Extrayendo geometrías de la capa local...')
                        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform
                        crs84 = QgsCoordinateReferenceSystem('EPSG:4326')
                        crs_layer = layer.crs()
                        transform = QgsCoordinateTransform(crs_layer, crs84, QgsProject.instance())
                        features_geojson = []
                        for feat in layer.getFeatures():
                            geom = feat.geometry()
                            if not geom.isNull():
                                if crs_layer != crs84:
                                    geom.transform(transform)
                                geom_json = json.loads(geom.asJson())
                                props = {}
                                for field in feat.fields():
                                    val = feat[field.name()]
                                    if hasattr(val, 'isValid') and not val.isValid():
                                        continue
                                    try:
                                        json.dumps(val)
                                        props[field.name()] = val
                                    except:
                                        pass
                                features_geojson.append({
                                    "type": "Feature",
                                    "geometry": geom_json,
                                    "properties": props
                                })
                        fc_dict = {
                            "type": "FeatureCollection",
                            "features": features_geojson
                        }
                        json_str = json.dumps(fc_dict)
                        mb = len(json_str) / 1024 / 1024
                        if mb > 9:
                            QMessageBox.warning(self, "geeSWAT", "La capa seleccionada es muy grande (más de 9MB en JSON) para enviarla directamente a la API de GEE. Exporte a Asset y use la opción ID de Asset GEE.")
                            return
                        subcuencas_data = {"type": "geojson", "data": fc_dict}
                    except Exception as e:
                        self._cn_log(f'Error al preparar capa: {str(e)}')

        # Bbox WGS84
        bbox = self._datos_ficha.get('_bbox_wgs84')
        if not bbox:
            from qgis.core import (QgsGeometry, QgsCoordinateReferenceSystem,
                                   QgsCoordinateTransform)
            geom  = QgsGeometry.fromWkt(self._aoi_geom)
            crs   = QgsCoordinateReferenceSystem(f'EPSG:{self._aoi_crs_epsg}')
            crs84 = QgsCoordinateReferenceSystem('EPSG:4326')
            if crs != crs84:
                geom.transform(QgsCoordinateTransform(crs, crs84, QgsProject.instance()))
            bb   = geom.boundingBox()
            bbox = (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())
        lon_min, lat_min, lon_max, lat_max = bbox
        
        cn_dir = os.path.join(carpeta, 'CN')
        os.makedirs(cn_dir, exist_ok=True)
        nombre_proy = self._inp_nombre_proy.text().strip()
        ts      = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        id_str  = f'{src["id"]}_{src["year"]}' if src.get('has_year') else src['id']
        fname   = f'{nombre_proy}_{id_str}_{ts}' if nombre_proy else f'{id_str}_{ts}'
        out_tif = os.path.join(cn_dir, fname + '.tif')
        out_csv = os.path.join(cn_dir, fname + '_Tabla_CN.csv')
        
        self._btn_cn_desc.setEnabled(False)
        self._btn_cn_cancel.setEnabled(True)
        self._prog_cn.setValue(0)
        self._txt_cn_log.clear()
        self._cn_log(f'Fuente          : {src["label"]}')
        self._cn_log(f'Asset GEE       : {src["collection"]}')
        self._cn_log(f'Escala efectiva : {esc_ef} m')
        self._cn_log(f'Proyeccion      : EPSG:{epsg}')
        self._cn_log(f'Bbox WGS84      : [{lon_min:.5f}, {lat_min:.5f}, {lon_max:.5f}, {lat_max:.5f}]')
        self._cn_log(f'Raster salida   : {out_tif}')
        if subcuencas_data:
            self._cn_log(f'CSV salida      : {out_csv}')
        self._cn_log('-' * 50)
        
        self._cn_worker = CNWorker(
            src, lon_min, lat_min, lon_max, lat_max,
            esc_ef, epsg, out_tif, out_csv, proyecto, subcuencas_data)
        self._cn_worker.log.connect(self._cn_log)
        self._cn_worker.progreso.connect(self._prog_cn.setValue)
        self._cn_worker.ok.connect(self._cn_completado)
        self._cn_worker.error.connect(self._cn_error)
        self._cn_worker.finished.connect(self._cn_worker_terminado)
        self._cn_worker.start()

    def _cancelar_cn(self):
        if self._cn_worker and self._cn_worker.isRunning():
            self._cn_worker.cancelar()
            self._cn_log('Cancelando...')

    def _cn_log(self, msg):
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._txt_cn_log.append(
            f'<span style="color:#95A5A6">[{ts}]</span> {msg}')
        sb = self._txt_cn_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _cn_completado(self, ruta_tif, ruta_csv, via_drive):
        if via_drive:
            self._cn_log('Exportacion a Google Drive completada.')
            QMessageBox.information(self, 'geeSWAT - CN Completado',
                'Curva Número se exportó a Google Drive.\n'
                'Descárgalo desde drive.google.com')
        else:
            self._cn_log(f'Raster guardado: {ruta_tif}')
            if ruta_csv:
                self._cn_log(f'Tabla CSV: {ruta_csv}')
            
            min_val, max_val = 1, 100
            
            try:
                # ── Cargar raster en QGIS ──
                rlayer = QgsRasterLayer(ruta_tif, 'CN - GCN250')
                if not rlayer.isValid():
                    self._cn_log('ERROR: El raster no es válido.')
                    return
                
                self._cn_log('Raster cargado correctamente.')
                
                # ── Calcular min/max REALES leyendo los píxeles ──
                # Usamos QgsRasterBlock para leer todos los valores y filtrar
                provider = rlayer.dataProvider()
                extent = rlayer.extent()
                cols = rlayer.width()
                rows = rlayer.height()
                
                self._cn_log(f'Dimensiones raster: {cols}x{rows} px')
                
                block = provider.block(1, extent, cols, rows)
                real_min = 999
                real_max = 0
                
                for r in range(rows):
                    for c in range(cols):
                        val = block.value(r, c)
                        # Solo considerar valores en el rango válido CN [1-100]
                        if 1 <= val <= 100:
                            if val < real_min:
                                real_min = val
                            if val > real_max:
                                real_max = val
                
                if real_min <= real_max and real_min >= 1:
                    min_val = int(real_min)
                    max_val = int(real_max)
                
                self._cn_log(f'Rango real del AOI: CN {min_val} - {max_val}')
                
                # ── Aplicar simbología Singleband Pseudocolor ──
                from qgis.core import (QgsColorRampShader, QgsRasterShader,
                                       QgsSingleBandPseudoColorRenderer)
                from qgis.PyQt.QtGui import QColor
                
                rango = max(max_val - min_val, 1)
                
                # Rampa de 5 clases: verde (bajo CN) → amarillo → rojo (alto CN)
                items = [
                    QgsColorRampShader.ColorRampItem(float(min_val), QColor(0, 104, 55), f'{min_val}'),
                    QgsColorRampShader.ColorRampItem(float(min_val + rango * 0.25), QColor(166, 217, 106), f'{int(min_val + rango * 0.25)}'),
                    QgsColorRampShader.ColorRampItem(float(min_val + rango * 0.50), QColor(255, 255, 191), f'{int(min_val + rango * 0.50)}'),
                    QgsColorRampShader.ColorRampItem(float(min_val + rango * 0.75), QColor(253, 174, 97), f'{int(min_val + rango * 0.75)}'),
                    QgsColorRampShader.ColorRampItem(float(max_val), QColor(215, 48, 39), f'{max_val}')
                ]
                
                fcn = QgsColorRampShader(float(min_val), float(max_val))
                fcn.setColorRampType(QgsColorRampShader.Interpolated)
                fcn.setColorRampItemList(items)
                
                shader = QgsRasterShader()
                shader.setRasterShaderFunction(fcn)
                
                renderer = QgsSingleBandPseudoColorRenderer(provider, 1, shader)
                renderer.setClassificationMin(float(min_val))
                renderer.setClassificationMax(float(max_val))
                
                rlayer.setRenderer(renderer)
                rlayer.triggerRepaint()
                
                self._cn_log(f'Simbología Pseudocolor aplicada: {min_val} - {max_val}')
                
                # ── Generar tabla CSV si no fue generada por GEE ──
                if not ruta_csv or not os.path.exists(ruta_csv):
                    try:
                        # Buscar la ruta de salida esperada
                        ruta_csv_local = ruta_tif.replace('.tif', '_Tabla_CN.csv')
                        
                        # Contar píxeles válidos y calcular estadísticas
                        total_validos = 0
                        suma = 0
                        for r in range(rows):
                            for c in range(cols):
                                val = block.value(r, c)
                                if 1 <= val <= 100:
                                    total_validos += 1
                                    suma += val
                        
                        promedio = suma / total_validos if total_validos > 0 else 0
                        
                        import csv
                        with open(ruta_csv_local, 'w', newline='', encoding='utf-8') as f:
                            writer = csv.writer(f)
                            writer.writerow(['Estadistica', 'Valor'])
                            writer.writerow(['CN_Min', min_val])
                            writer.writerow(['CN_Max', max_val])
                            writer.writerow(['CN_Promedio', f'{promedio:.2f}'])
                            writer.writerow(['Pixeles_Validos', total_validos])
                            writer.writerow(['Pixeles_Totales', rows * cols])
                        
                        ruta_csv = ruta_csv_local
                        self._cn_log(f'Tabla CN generada: {ruta_csv}')
                    except Exception as et:
                        self._cn_log(f'Error al generar tabla: {et}')
                
                QgsProject.instance().addMapLayer(rlayer)
                self._cn_log('Capa añadida al proyecto QGIS.')
                
            except Exception as ex:
                self._cn_log(f'Error al procesar raster: {ex}')
                import traceback
                self._cn_log(traceback.format_exc())
            
            # ── Mensaje final ──
            msg_parts = [f'Raster Curva Número:\n  {ruta_tif}',
                         f'  Rango CN: {min_val} - {max_val}']
            if ruta_csv and os.path.exists(ruta_csv):
                msg_parts.append(f'\nTabla CN:\n  {ruta_csv}')
            else:
                msg_parts.append(f'\nTabla CN:\n  (no generada)')
            
            QMessageBox.information(self, 'geeSWAT - CN Completado',
                                    '\n'.join(msg_parts))

    def _cn_error(self, msg):
        self._cn_log(f'ERROR: {msg}')
        QMessageBox.critical(self, 'geeSWAT - Error CN', msg)

    def _cn_worker_terminado(self):
        self._btn_cn_desc.setEnabled(True)
        self._btn_cn_cancel.setEnabled(False)

    # ══════════════════════════════════════════════════════════════════════════
    # PESTAÑA 6 - ACERCA DE
    # ══════════════════════════════════════════════════════════════════════════
    def _tab_acerca(self):

        w = QWidget()
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget(); inner.setStyleSheet('background:#F5F7FA;')
        vl = QVBoxLayout(inner); vl.setContentsMargins(0,0,0,0); vl.setSpacing(0)

        # Hero
        hero = QWidget(); hero.setFixedHeight(170)
        hero.setStyleSheet(
            'background:qlineargradient(x1:0,y1:0,x2:1,y2:1,'
            'stop:0 #0A2F6E, stop:0.5 #1565C0, stop:1 #0D47A1);border:none;')
        hero_hl = QHBoxLayout(hero)
        hero_hl.setContentsMargins(36, 24, 36, 24); hero_hl.setSpacing(24)

        lbl_ic = QLabel(); lbl_ic.setFixedSize(90, 90)
        lbl_ic.setPixmap(self._generar_icono_cuenca(90))
        lbl_ic.setAlignment(Qt.AlignCenter)
        hero_hl.addWidget(lbl_ic)

        hvl = QVBoxLayout(); hvl.setSpacing(5)
        lbl_t = QLabel('geeSWAT')
        lbl_t.setStyleSheet('font-size:30px;font-weight:bold;color:white;letter-spacing:2px;background:transparent;border:none;')
        hvl.addWidget(lbl_t)
        lbl_s = QLabel('SWAT Hidrológico con Google Earth Engine para QGIS 3.x / 4.x')
        lbl_s.setStyleSheet('font-size:11px;color:#BBDEFB;background:transparent;border:none;')
        hvl.addWidget(lbl_s)
        lbl_v = QLabel('  QGIS 3.16+  —  GPL-2.0  ')
        lbl_v.setStyleSheet('font-size:10px;font-weight:bold;color:#0D47A1;background:#BBDEFB;border-radius:10px;padding:3px 10px;border:none;')
        lbl_v.setFixedHeight(22); hvl.addWidget(lbl_v)
        hero_hl.addLayout(hvl); hero_hl.addStretch()
        vl.addWidget(hero)

        inner2 = QWidget(); inner2.setStyleSheet('background:#F5F7FA;border:none;')
        vl2 = QVBoxLayout(inner2); vl2.setContentsMargins(28,22,28,28); vl2.setSpacing(16)

        # Descripción
        desc = QWidget()
        desc.setStyleSheet('background:white;border:1px solid #E0E0E0;border-radius:10px;')
        dl = QVBoxLayout(desc); dl.setContentsMargins(20,16,20,16)
        lbl_d = QLabel(
            '<b>geeSWAT</b> conecta QGIS con <b>Google Earth Engine</b> para automatizar '
            'la descarga y preprocesamiento de datos espaciales para modelado SWAT.<br><br>'
            '✔ <b>AOI:</b> Selecciona el área de estudio desde una capa vectorial o dibujando en el mapa.<br>'
            '✔ <b>MDE:</b> Descarga DEMs de diversas fuentes globales (SRTM, Copernicus, MERIT, ALOS...).<br>'
            '✔ <b>Nuevos Productos:</b> Descarga directa de mapas de <b>Suelos (SOIL)</b>, <b>Uso de Suelo (LANDUSE)</b>, series de <b>Datos Climáticos</b>, proyecciones de <b>Cambio Climático</b> y generación de <b>Curva Número (CN)</b>.<br>'
            '✔ <b>Gestión:</b> Archivos organizados automáticamente en subcarpetas (<b>DEM/</b>, <b>SOIL/</b>, <b>LANDUSE/</b>, etc.).<br>'
            '✔ <b>Eficiencia:</b> Para áreas extensas usa Google Drive con monitoreo en tiempo real.')
        lbl_d.setWordWrap(True); lbl_d.setTextFormat(Qt.RichText)
        lbl_d.setStyleSheet('font-size:11px;color:#37474F;background:transparent;border:none;')
        dl.addWidget(lbl_d)
        vl2.addWidget(desc)

        # Configuración SWAT
        swat_conf = QWidget()
        swat_conf.setStyleSheet('background:white;border:1px solid #E0E0E0;border-radius:10px;')
        scl = QVBoxLayout(swat_conf); scl.setContentsMargins(20,16,20,16)
        
        lbl_swat_titulo = QLabel('<b>Configuración SWAT</b>')
        lbl_swat_titulo.setStyleSheet('font-size:12px;color:#0D47A1;background:transparent;border:none;')
        scl.addWidget(lbl_swat_titulo)

        lbl_swat_desc = QLabel(
            '<b>1.</b> Actualizar base de datos SWAT2012, remplazando la ruta: <code>C:\\SWAT\\SWATEditor\\Databases</code> '
            '(le comparto los dos archivos en este link: <a href="https://drive.google.com/drive/folders/1yAIshgRJduXupw6deSXzzGYyA156q17t?usp=drive_link" style="color:#1565C0;">Google Drive</a>).<br>'
            '<b>2.</b> Datos SWAT: <a href="https://swat.tamu.edu/data/" style="color:#1565C0;">https://swat.tamu.edu/data/</a> para actualizar Global Weather Database.<br>'
            '<b>Recomendación:</b> configurar formato idioma en Ingles (Estados Unidos) en Windows '
            '(<code>Win + R</code> escribir <code>intl.cpl</code> se abrirá una ventana de Región y configuramos).'
        )
        lbl_swat_desc.setWordWrap(True); lbl_swat_desc.setTextFormat(Qt.RichText)
        lbl_swat_desc.setOpenExternalLinks(True)
        lbl_swat_desc.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        lbl_swat_desc.setStyleSheet('font-size:11px;color:#37474F;background:transparent;border:none;')
        scl.addWidget(lbl_swat_desc)
        vl2.addWidget(swat_conf)

        # Botones rápidos
        hl_b = QHBoxLayout(); hl_b.setSpacing(12)
        b1 = QPushButton('Abrir www.geomatica.pe'); b1.setStyleSheet(_btn_primario())
        b1.setMinimumHeight(36)
        b1.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://www.geomatica.pe')))
        b2 = QPushButton('Registrarse en GEE'); b2.setStyleSheet(_btn_secundario())
        b2.setMinimumHeight(36)
        b2.clicked.connect(lambda: QDesktopServices.openUrl(QUrl('https://earthengine.google.com')))
        hl_b.addWidget(b1); hl_b.addWidget(b2); hl_b.addStretch()
        vl2.addLayout(hl_b)

        # ── Tarjeta de contacto ───────────────────────────────────────────────
        contacto = QWidget()
        contacto.setStyleSheet(
            'background:white;border:1px solid #E0E0E0;border-radius:10px;')
        cl = QVBoxLayout(contacto); cl.setContentsMargins(0, 0, 0, 0); cl.setSpacing(0)

        # Cabecera de la tarjeta
        hdr = QWidget()
        hdr.setStyleSheet(
            'background:qlineargradient(x1:0,y1:0,x2:1,y2:0,'
            'stop:0 #0D47A1,stop:1 #1565C0);'
            'border-radius:9px 9px 0 0;')
        hdr.setFixedHeight(46)
        hdr_hl = QHBoxLayout(hdr); hdr_hl.setContentsMargins(18, 0, 18, 0)
        lbl_hdr = QLabel('Contacto del desarrollador')
        lbl_hdr.setStyleSheet(
            'color:white;font-size:12px;font-weight:bold;'
            'background:transparent;border:none;')
        hdr_hl.addWidget(lbl_hdr); hdr_hl.addStretch()
        cl.addWidget(hdr)

        # Cuerpo de la tarjeta
        body = QWidget()
        body.setStyleSheet('background:transparent;border:none;')
        bl = QGridLayout(body); bl.setContentsMargins(18, 14, 18, 16); bl.setSpacing(10)
        bl.setColumnMinimumWidth(0, 26)

        def _fila(row, icono, texto, url=None):
            lbl_ico = QLabel(icono)
            lbl_ico.setStyleSheet(
                f'color:{AZUL_M};font-size:14px;background:transparent;border:none;')
            lbl_ico.setFixedWidth(26)
            bl.addWidget(lbl_ico, row, 0)
            lbl_txt = QLabel(texto)
            lbl_txt.setTextFormat(Qt.RichText)
            lbl_txt.setOpenExternalLinks(True)
            lbl_txt.setStyleSheet(
                'color:#263238;font-size:11px;background:transparent;border:none;')
            lbl_txt.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
            bl.addWidget(lbl_txt, row, 1)

        _fila(0, 'N', '<b>Nino Bravo Morales</b>')
        _fila(1, 'E', 'Especialista en Geomatica')
        _fila(2, 'T', '+51 995 664 488')
        _fila(3, '@',
              '<a href="mailto:nino@geomatica.pe" style="color:#1565C0;">'
              'nino@geomatica.pe</a>')
        _fila(4, 'W',
              '<a href="https://www.geomatica.pe" style="color:#1565C0;">'
              'www.geomatica.pe</a>')

        # Botón WhatsApp
        btn_wa = QPushButton('Contactar por WhatsApp  +51 995 664 488')
        btn_wa.setStyleSheet(
            'QPushButton {background:#25D366;color:white;border-radius:6px;'
            'font-size:11px;font-weight:bold;padding:7px 14px;border:none;}'
            'QPushButton:hover {background:#1EBE57;}')
        btn_wa.setMinimumHeight(36)
        btn_wa.setCursor(Qt.PointingHandCursor)
        btn_wa.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl('https://wa.me/51995664488?text=Hola%20Nino%2C%20consulta%20sobre%20geeSWAT')))

        btn_mail = QPushButton('Enviar correo')
        btn_mail.setStyleSheet(
            'QPushButton {background:#1565C0;color:white;border-radius:6px;'
            'font-size:11px;font-weight:bold;padding:7px 14px;border:none;}'
            'QPushButton:hover {background:#0D47A1;}')
        btn_mail.setMinimumHeight(36)
        btn_mail.setCursor(Qt.PointingHandCursor)
        btn_mail.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl('mailto:nino@geomatica.pe?subject=Consulta%20geeSWAT')))

        btn_row = QHBoxLayout(); btn_row.setSpacing(10)
        btn_row.addWidget(btn_wa); btn_row.addWidget(btn_mail); btn_row.addStretch()
        bl.addLayout(btn_row, 5, 0, 1, 2)

        cl.addWidget(body)
        vl2.addWidget(contacto)

        # Pie de página
        pie = QLabel(
            '<center><span style="color:#BDBDBD;font-size:9px;">'
            'geeSWAT v1.2.3 &mdash; GPL-2.0 &mdash; 2026 Geomatica Ambiental'
            '</span></center>')
        pie.setTextFormat(Qt.RichText); pie.setAlignment(Qt.AlignCenter)
        pie.setStyleSheet('background:transparent;border:none;margin-top:6px;')
        vl2.addWidget(pie); vl2.addStretch()


        vl.addWidget(inner2)
        scroll.setWidget(inner)
        lv = QVBoxLayout(w); lv.setContentsMargins(0,0,0,0); lv.addWidget(scroll)
        return w

    # ══════════════════════════════════════════════════════════════════════════
    # LÓGICA GEE  (todos los calls de red corren en QThread — nunca en el hilo UI)
    # ══════════════════════════════════════════════════════════════════════════
    def _auto_conectar_gee(self):
        """Intento silencioso de reconexión al abrir el diálogo."""
        if not EE_DISPONIBLE:
            return
        proyecto = self._inp_project.text().strip()
        if not proyecto:
            return
        # Lanzar en background — sin bloquear QGIS
        self._lanzar_init_worker(proyecto, silencioso=True)

    def _lanzar_init_worker(self, proyecto, silencioso=False):
        """Crea y arranca un GeeInitWorker para ee.Initialize en background."""
        if self._gee_worker and self._gee_worker.isRunning():
            return
        self._gee_worker = _GeeInitWorker(proyecto)
        self._gee_worker.ok.connect(
            lambda p: self._on_init_ok(p, silencioso))
        if not silencioso:
            self._gee_worker.error.connect(self._on_init_error)
        self._gee_worker.start()

    # ── Callbacks del worker Init ─────────────────────────────────────────────
    def _on_init_ok(self, proyecto, silencioso):
        self._set_barra(True, proyecto)
        self._lbl_gee_msg.setText('✔ Conexión automática exitosa.' if silencioso
                                  else '✔ Conexión GEE verificada correctamente.')
        self._lbl_gee_msg.setStyleSheet(f'color:{VERDE};font-size:10px;')
        if not silencioso:
            QMessageBox.information(
                self, 'geeSWAT',
                f'✔ Conexión a Google Earth Engine exitosa.\nProyecto: {proyecto or "(credenciales locales)"}')

    def _on_init_error(self, msg):
        self._set_barra(False)
        self._lbl_gee_msg.setText(f'✖ Error: {msg[:90]}')
        self._lbl_gee_msg.setStyleSheet(f'color:{ROJO};font-size:10px;')
        # Restaurar botón verificar
        self._btn_verificar.setEnabled(True)
        self._btn_verificar.setText('🔗  Verificar conexión')
        if any(k in msg.lower() for k in ('credentials', 'oauth', 'token', 'authorize')):
            QMessageBox.warning(self, 'Sin credenciales',
                'No se encontraron credenciales GEE.\n'
                'Haz clic en "Autenticar con Google" primero.')
        else:
            QMessageBox.critical(self, 'Error de conexión', f'No se pudo conectar:\n\n{msg}')

    def _autenticar_gee(self):
        if not EE_DISPONIBLE:
            QMessageBox.warning(self, 'geeSWAT',
                'El módulo earthengine-api no está instalado.\n'
                'Usa la opción "Instalar dependencias" del menú geeSWAT.')
            return
        self._btn_auth.setEnabled(False)
        self._btn_auth.setText('⏳  Autenticando...')
        self._lbl_gee_msg.setText('Abriendo navegador para autenticación...')
        self._lbl_gee_msg.setStyleSheet(f'color:{GRIS};font-size:10px;')
        # ee.Authenticate() abre el navegador y espera — corre en background
        worker = _GeeAuthWorker()
        worker.ok.connect(self._on_auth_ok)
        worker.error.connect(self._on_auth_error)
        worker.finished.connect(lambda: (
            self._btn_auth.setEnabled(True),
            self._btn_auth.setText('🔑  Autenticar con Google')))
        # Guardar referencia para evitar GC
        self._auth_worker = worker
        worker.start()

    def _on_auth_ok(self):
        self._lbl_gee_msg.setText('✔ Autenticación completada. Haz clic en "Verificar conexión".')
        self._lbl_gee_msg.setStyleSheet(f'color:{VERDE};font-size:10px;')
        QMessageBox.information(self, 'Autenticación',
            '✔ Autenticación completada.\nHaz clic en "Verificar conexión".')

    def _on_auth_error(self, msg):
        self._lbl_gee_msg.setText(f'✖ Error: {msg[:90]}')
        self._lbl_gee_msg.setStyleSheet(f'color:{ROJO};font-size:10px;')
        QMessageBox.critical(self, 'Error de autenticación', msg)

    def _verificar_gee(self):
        if not EE_DISPONIBLE:
            QMessageBox.warning(self, 'geeSWAT', 'El módulo earthengine-api no está instalado.')
            return
        if self._gee_worker and self._gee_worker.isRunning():
            QMessageBox.information(self, 'geeSWAT', 'Ya hay una verificación en curso...')
            return
        self._btn_verificar.setEnabled(False)
        self._btn_verificar.setText('⏳  Verificando...')
        self._lbl_gee_msg.setText('Conectando con GEE...')
        self._lbl_gee_msg.setStyleSheet(f'color:{GRIS};font-size:10px;')
        proyecto = self._inp_project.text().strip()
        self._lanzar_init_worker(proyecto, silencioso=False)

    def _set_barra(self, ok, proyecto=''):
        if ok:
            txt = f'  ✔  GEE conectado  ·  proyecto: {proyecto}' if proyecto else '  ✔  GEE conectado'
            self._barra_estado.setText(txt)
            self._barra_estado.setStyleSheet(
                f'background:{VERDE_M};color:white;padding:7px 14px;font-size:11px;font-weight:bold;')
        else:
            self._barra_estado.setText('  ⬤  Sin conexión a GEE — Configure en la pestaña Config GEE')
            self._barra_estado.setStyleSheet(
                f'background:{ROJO};color:white;padding:7px 14px;font-size:11px;font-weight:bold;')

    # ══════════════════════════════════════════════════════════════════════════
    # CONFIGURACIÓN
    # ══════════════════════════════════════════════════════════════════════════
    def _seleccionar_carpeta(self):
        d = QFileDialog.getExistingDirectory(
            self, 'Seleccionar carpeta de salida',
            self._inp_carpeta.text() or os.path.expanduser('~'))
        if d:
            self._inp_carpeta.setText(d)
            self._actualizar_ruta_salida()

    def _guardar_config(self):
        self._settings.setValue('gmail',   self._inp_gmail.text().strip())
        self._settings.setValue('project', self._inp_project.text().strip())
        self._settings.setValue('nombre',  self._inp_nombre_proy.text().strip())
        self._settings.setValue('carpeta', self._inp_carpeta.text().strip())
        self._settings.setValue('epsg',    self._inp_epsg.text().strip())
        QMessageBox.information(self, 'geeSWAT', '✔ Configuración guardada.')
        self._actualizar_ruta_salida()

    def _cargar_config(self):
        self._inp_gmail.setText(       self._settings.value('gmail',   ''))
        self._inp_project.setText(     self._settings.value('project', ''))
        self._inp_nombre_proy.setText( self._settings.value('nombre',  ''))
        self._inp_carpeta.setText(     self._settings.value('carpeta', ''))
        self._inp_epsg.setText(        self._settings.value('epsg',    ''))
        self._actualizar_ruta_salida()

    def _get_epsg(self):
        """Devuelve el codigo EPSG como entero, o None si no esta configurado."""
        v = self._inp_epsg.text().strip()
        if not v:
            return None
        try:
            return int(v.upper().replace('EPSG:', '').strip())
        except ValueError:
            return None

    def _actualizar_ruta_salida(self):
        # Guard: widget may not exist yet during dialog construction
        if not hasattr(self, '_lbl_ruta_salida'):
            return
        carpeta = self._inp_carpeta.text().strip()
        epsg    = self._inp_epsg.text().strip() if hasattr(self, '_inp_epsg') else ''
        if carpeta:
            dem_dir  = os.path.join(carpeta, 'DEM')
            soil_dir = os.path.join(carpeta, 'SOIL')
            crs_txt  = f'  |  CRS: EPSG:{epsg}' if epsg else '  |  CRS: EPSG:4326 (sin configurar)'
            self._lbl_ruta_salida.setText(
                f'DEM  -> {dem_dir}\n'
                f'SOIL -> {soil_dir}{crs_txt}')
        else:
            self._lbl_ruta_salida.setText('Ruta: (configura la carpeta de salida en Config GEE)')

    # ══════════════════════════════════════════════════════════════════════════
    # AOI
    # ══════════════════════════════════════════════════════════════════════════
    def _refrescar_capas(self):
        self._cmb_capas.blockSignals(True)
        self._cmb_capas.clear()
        self._cmb_capas.addItem('— Seleccionar capa vectorial —', None)
        for lid, capa in QgsProject.instance().mapLayers().items():
            if isinstance(capa, QgsVectorLayer):
                self._cmb_capas.addItem(f'{capa.name()}  [{capa.geometryType()}]', lid)
        self._cmb_capas.blockSignals(False)

    def _usar_capa(self):
        idx = self._cmb_capas.currentIndex()
        if idx <= 0:
            QMessageBox.warning(self, 'Sin capa', 'Selecciona una capa vectorial primero.')
            return
        lid   = self._cmb_capas.currentData()
        capa  = QgsProject.instance().mapLayer(lid)
        if not capa:
            return
        geoms = [f.geometry() for f in capa.getFeatures()
                 if f.geometry() and not f.geometry().isEmpty()]
        if not geoms:
            QMessageBox.warning(self, 'Sin geometría', 'La capa no tiene geometrías válidas.')
            return
        geom = geoms[0]
        for g in geoms[1:]:
            geom = geom.combine(g)
        self._aoi_geom     = geom.asWkt()
        self._aoi_crs_epsg = capa.crs().postgisSrid()
        self._dibujar_rubber(geom, capa.crs())
        self._calcular_ficha_aoi(geom, capa.crs())
        self._actualizar_estado_aoi()

    def _activar_dibujo(self):
        self._puntos_poly = []
        if self._rubber:
            self._rubber.reset()
        self._rubber = QgsRubberBand(self.canvas, QgsWkbTypes.LineGeometry)
        self._rubber.setColor(QColor(21, 101, 192, 160))
        self._rubber.setWidth(2)
        self._tool_dibujo = QgsMapToolEmitPoint(self.canvas)
        self._tool_dibujo.canvasClicked.connect(self._click_mapa)
        self.canvas.setMapTool(self._tool_dibujo)
        self._btn_dibujar.setText('✏  Dibujando... (doble clic o clic derecho para cerrar)')
        self._btn_dibujar.setStyleSheet(_btn_peligro())
        self._click_count = 0
        self.showMinimized()

    def _click_mapa(self, point, button):
        self._click_count = getattr(self, '_click_count', 0) + 1
        if button == Qt.RightButton or self._click_count > 1:
            if len(self._puntos_poly) >= 3:
                self._cerrar_poligono()
            return
        self._puntos_poly.append(QgsPointXY(point.x(), point.y()))
        self._rubber.addPoint(point)
        self._click_count = 0

    def _cerrar_poligono(self):
        if len(self._puntos_poly) < 3:
            return
        ring = [(p.x(), p.y()) for p in self._puntos_poly]
        ring.append(ring[0])
        wkt  = 'POLYGON((' + ','.join(f'{x} {y}' for x, y in ring) + '))'
        geom = QgsGeometry.fromWkt(wkt)
        self._aoi_geom     = wkt
        crs = self.canvas.mapSettings().destinationCrs()
        self._aoi_crs_epsg = crs.postgisSrid()
        self._dibujar_rubber(geom, crs)
        self._calcular_ficha_aoi(geom, crs)
        self.canvas.unsetMapTool(self._tool_dibujo)
        self._btn_dibujar.setText('✏  Activar dibujo de polígono')
        self._btn_dibujar.setStyleSheet(_btn_primario(CYAN, VERDE))
        self.showNormal(); self.raise_(); self.activateWindow()
        self._actualizar_estado_aoi()

    def _dibujar_rubber(self, geom, crs):
        if self._rubber:
            self._rubber.reset()
        self._rubber = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber.setColor(QColor(13, 71, 161, 70))
        self._rubber.setStrokeColor(QColor(21, 101, 192, 220))
        self._rubber.setWidth(2)
        dst = self.canvas.mapSettings().destinationCrs()
        if crs != dst:
            geom_t = QgsGeometry(geom)
            geom_t.transform(QgsCoordinateTransform(crs, dst, QgsProject.instance()))
            self._rubber.setToGeometry(geom_t, dst)
        else:
            self._rubber.setToGeometry(geom, crs)

    def _limpiar_aoi(self):
        self._aoi_geom = None; self._aoi_crs_epsg = None
        if self._rubber: self._rubber.reset()
        self._datos_ficha = {}
        self._limpiar_ficha()
        self._actualizar_estado_aoi()
        # Actualizar panel MDE
        self._lbl_mde_aoi.setText('Sin AOI definida — ve a la pestaña "Seleccionar AOI"')
        self._lbl_mde_aoi.setStyleSheet(
            f'background:{ROJO_C};color:{ROJO};padding:8px;border-radius:5px;font-size:10px;')

    def _zoom_aoi(self):
        if not self._aoi_geom or not self._aoi_crs_epsg:
            return
        try:
            geom = QgsGeometry.fromWkt(self._aoi_geom)
            crs  = QgsCoordinateReferenceSystem(f'EPSG:{self._aoi_crs_epsg}')
            dst  = self.canvas.mapSettings().destinationCrs()
            if crs != dst:
                geom.transform(QgsCoordinateTransform(crs, dst, QgsProject.instance()))
            ext = geom.boundingBox()
            ext.grow(ext.width() * 0.1)
            self.canvas.setExtent(ext)
            self.canvas.refresh()
        except Exception as ex:
            QMessageBox.warning(self, 'geeSWAT', f'Error al hacer zoom: {ex}')

    def _actualizar_estado_aoi(self):
        tiene = bool(self._aoi_geom)
        self._btn_zoom_aoi.setEnabled(tiene)
        self._btn_limpiar_aoi.setEnabled(tiene)
        self._btn_ir_mde.setEnabled(tiene)
        if tiene and self._datos_ficha:
            area = self._datos_ficha.get('Area', '?')
            self._lbl_aoi_estado.setText(f'✔ AOI definida\nÁrea: {area}')
            self._lbl_aoi_estado.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};border-radius:6px;padding:6px;font-size:10px;')
            # Actualizar label en pestaña MDE
            self._lbl_mde_aoi.setText(
                f'✔ AOI definida — Área: {area}\n'
                f'Bbox: [{self._datos_ficha.get("Bbox Oeste","?")} , '
                f'{self._datos_ficha.get("Bbox Sur","?")} , '
                f'{self._datos_ficha.get("Bbox Este","?")} , '
                f'{self._datos_ficha.get("Bbox Norte","?")}]')
            self._lbl_mde_aoi.setStyleSheet(
                f'background:{VERDE_C};color:{VERDE};padding:8px;border-radius:5px;font-size:10px;')
        else:
            self._lbl_aoi_estado.setText('Sin AOI definida')
            self._lbl_aoi_estado.setStyleSheet(
                f'background:#ECEFF1;color:{GRIS};border-radius:6px;padding:6px;font-size:10px;')

    def _calcular_ficha_aoi(self, geom, crs):
        try:
            d = QgsDistanceArea()
            d.setSourceCrs(crs, QgsProject.instance().transformContext())
            d.setEllipsoid('WGS84')
            area_m2  = d.measureArea(geom)
            area_ha  = area_m2 / 10000.0
            area_km2 = area_m2 / 1e6
            perim_m  = d.measurePerimeter(geom)
            perim_km = perim_m / 1000.0

            geom_ll = QgsGeometry(geom)
            crs84   = QgsCoordinateReferenceSystem('EPSG:4326')
            if crs != crs84:
                geom_ll.transform(QgsCoordinateTransform(crs, crs84, QgsProject.instance()))
            centro  = geom_ll.centroid().asPoint()
            lat, lon= centro.y(), centro.x()

            zona_num = int((lon + 180) / 6) + 1
            hemis    = 'N' if lat >= 0 else 'S'
            zona_utm = f'{zona_num}{hemis}'
            epsg_utm = (32600 + zona_num) if lat >= 0 else (32700 + zona_num)

            bb = geom_ll.boundingBox()
            n_verts = 0
            try:
                wt = QgsWkbTypes.geometryType(geom.wkbType())
                if wt == QgsWkbTypes.PolygonGeometry:
                    if QgsWkbTypes.isMultiType(geom.wkbType()):
                        mp = geom.asMultiPolygon()
                        n_verts = len(mp[0][0]) if mp else 0
                    else:
                        p = geom.asPolygon()
                        n_verts = len(p[0]) if p else 0
            except Exception:  # nosec
                pass

            # Alerta área
            if area_ha < 1:
                alerta = '⚠ Área muy pequeña (mínimo recomendado: 1 ha)'
                c_alerta = NARANJA
            elif area_ha > 50000:
                alerta = '⛔ Área grande — se usará Google Drive para la descarga'
                c_alerta = ROJO
            elif area_ha > 5000:
                alerta = '🟡 Área grande — resolución reducida automáticamente'
                c_alerta = '#F57F17'
            else:
                alerta = '✅ Área dentro del rango óptimo para descarga directa'
                c_alerta = VERDE_M

            self._lbl_alerta_area.setText(alerta)
            self._lbl_alerta_area.setStyleSheet(
                f'background:{c_alerta}22;color:{c_alerta};'
                f'border-radius:5px;padding:6px;font-size:10px;'
                f'border:1px solid {c_alerta};')
            self._lbl_alerta_area.setVisible(True)

            self._datos_ficha = {
                'Area'         : f'{area_ha:.2f} ha  /  {area_km2:.4f} km²',
                'Perímetro'    : f'{perim_km:.3f} km',
                'Centroide Lat': f'{lat:.6f}°',
                'Centroide Lon': f'{lon:.6f}°',
                'Zona UTM'     : zona_utm,
                'Bbox Norte'   : f'{bb.yMaximum():.6f}°',
                'Bbox Sur'     : f'{bb.yMinimum():.6f}°',
                'Bbox Este'    : f'{bb.xMaximum():.6f}°',
                'Bbox Oeste'   : f'{bb.xMinimum():.6f}°',
                'Nº vértices'  : str(n_verts),
                '_bbox_wgs84'  : (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum()),
            }
            self._renderizar_ficha()
        except Exception as ex:
            QMessageBox.warning(self, 'geeSWAT', f'Error al calcular ficha AOI:\n{ex}')

    def _renderizar_ficha(self):
        # Limpiar layout ficha
        while self._ficha_layout.count():
            item = self._ficha_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not self._datos_ficha:
            self._lbl_sin_aoi = QLabel('Define un area de interes\npara ver su ficha geometrica.')
            self._lbl_sin_aoi.setAlignment(Qt.AlignCenter)
            self._lbl_sin_aoi.setStyleSheet(f'color:#9E9E9E;font-size:11px;padding:40px;')
            self._ficha_layout.addWidget(self._lbl_sin_aoi)
            self._ficha_layout.addStretch()
            return

        for k, v in self._datos_ficha.items():
            if k.startswith('_'):
                continue
            fila = QHBoxLayout()
            lk = QLabel(k + ':')
            lk.setStyleSheet(f'font-weight:bold;color:{AZUL};font-size:10px;min-width:110px;')
            lk.setFixedWidth(120)
            lv = QLabel(str(v))
            lv.setStyleSheet(f'color:{GRIS_OS};font-size:10px;')
            lv.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lv.setCursor(Qt.IBeamCursor)
            lv.setWordWrap(True)
            fila.addWidget(lk); fila.addWidget(lv, 1)
            self._ficha_layout.addLayout(fila)

            sep = QFrame(); sep.setFrameShape(QFrame.HLine)
            sep.setStyleSheet('color:#F5F5F5;background:#F5F5F5;border:none;max-height:1px;')
            self._ficha_layout.addWidget(sep)

        self._ficha_layout.addStretch()

    def _limpiar_ficha(self):
        while self._ficha_layout.count():
            item = self._ficha_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self._lbl_sin_aoi = QLabel('Define un area de interes\npara ver su ficha geometrica.')
        self._lbl_sin_aoi.setAlignment(Qt.AlignCenter)
        self._lbl_sin_aoi.setStyleSheet(f'color:#9E9E9E;font-size:11px;padding:40px;')
        self._ficha_layout.addWidget(self._lbl_sin_aoi)
        self._ficha_layout.addStretch()
        self._lbl_alerta_area.setVisible(False)

    # ══════════════════════════════════════════════════════════════════════════
    # DEM — parámetros
    # ══════════════════════════════════════════════════════════════════════════
    def _actualizar_info_dem(self):
        idx = self._cmb_dem.currentIndex()
        if idx < 0 or idx >= len(GEE_DEMS):
            return
        d    = GEE_DEMS[idx]
        esc  = self._spn_escala.value()
        esc_ef = esc if esc > 0 else d['scale']
        self._lbl_dem_desc.setText(
            f'<b>Colección GEE:</b> {d["collection"]}<br>'
            f'<b>Banda:</b> {d["band"]}  |  <b>Tipo:</b> {d["dtype"]}  |  <b>Res. nativa:</b> {d["scale"]} m<br>'
            f'<b>Info:</b> {d["desc"]}<br>'
            f'<b>Escala efectiva:</b> {esc_ef} m' +
            (' <i>(nativa)</i>' if esc == 0 else ''))
        self._lbl_escala_info.setText(
            f'0 = resolución nativa ({d["scale"]} m). '
            f'Escala efectiva: {esc_ef} m.')
        self._actualizar_ruta_salida()

    # ══════════════════════════════════════════════════════════════════════════
    # DESCARGA MDE
    # ══════════════════════════════════════════════════════════════════════════
    def _ejecutar_descarga(self):
        # Validaciones
        if not self._aoi_geom:
            QMessageBox.warning(self, 'geeSWAT',
                'Define primero el área de interés en la pestaña "Seleccionar AOI".')
            self._tabs.setCurrentIndex(1)
            return

        carpeta = self._inp_carpeta.text().strip()
        if not carpeta:
            QMessageBox.warning(self, 'geeSWAT',
                'Configura la carpeta de salida en la pestaña "Config GEE".')
            self._tabs.setCurrentIndex(0)
            return

        proyecto = self._inp_project.text().strip()
        gmail    = self._inp_gmail.text().strip()
        idx_dem  = self._cmb_dem.currentIndex()
        dem_info = GEE_DEMS[idx_dem]
        escala   = self._spn_escala.value()
        scale_eff= escala if escala > 0 else dem_info['scale']

        # Obtener bbox WGS84
        bbox = self._datos_ficha.get('_bbox_wgs84')
        if not bbox:
            # Calcular desde WKT
            geom = QgsGeometry.fromWkt(self._aoi_geom)
            crs  = QgsCoordinateReferenceSystem(f'EPSG:{self._aoi_crs_epsg}')
            crs84= QgsCoordinateReferenceSystem('EPSG:4326')
            if crs != crs84:
                geom.transform(QgsCoordinateTransform(crs, crs84, QgsProject.instance()))
            bb  = geom.boundingBox()
            bbox= (bb.xMinimum(), bb.yMinimum(), bb.xMaximum(), bb.yMaximum())

        lon_min, lat_min, lon_max, lat_max = bbox
        
        epsg = self._get_epsg() or 4326

        # Crear carpeta DEM
        dem_dir = os.path.join(carpeta, 'DEM')
        os.makedirs(dem_dir, exist_ok=True)

        # Nombre del archivo de salida
        nombre_proy = self._inp_nombre_proy.text().strip()
        ts    = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        slug  = dem_info['collection'].replace('/', '_').replace(' ', '_')
        fname = f'{nombre_proy}_{slug}_{ts}.tif' if nombre_proy else f'{slug}_{ts}.tif'
        output_path = os.path.join(dem_dir, fname)

        # Iniciar worker
        self._btn_descargar.setEnabled(False)
        self._btn_cancelar_mde.setEnabled(True)
        self._progreso_mde.setValue(0)
        self._txt_log.clear()
        self._log(f'Iniciando descarga: {dem_info["label"]}')
        self._log(f'Escala efectiva   : {scale_eff} m')
        self._log(f'Bbox WGS84        : [{lon_min:.5f}, {lat_min:.5f}, {lon_max:.5f}, {lat_max:.5f}]')
        self._log(f'Proyeccion        : EPSG:{epsg}')
        self._log(f'Carpeta de salida : {dem_dir}')
        self._log(f'Archivo           : {fname}')
        self._log('─' * 50)

        self._worker = MDEWorker(
            dem_info, lon_min, lat_min, lon_max, lat_max,
            scale_eff, output_path, proyecto, gmail, epsg)
        self._worker.log.connect(self._log)
        self._worker.progreso.connect(self._progreso_mde.setValue)
        self._worker.ok.connect(self._descarga_completada)
        self._worker.error.connect(self._descarga_error)
        self._worker.finished.connect(self._worker_terminado)
        self._worker.start()

    def _cancelar_descarga(self):
        if self._worker and self._worker.isRunning():
            self._worker.cancelar()
            self._log('⚠ Cancelando...')

    def _log(self, msg):
        ts = datetime.datetime.now().strftime('%H:%M:%S')
        self._txt_log.append(f'<span style="color:#95A5A6">[{ts}]</span> {msg}')
        sb = self._txt_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _descarga_completada(self, ruta, via_drive):
        if via_drive:
            self._log(f'✔ Exportación a Google Drive completada: {ruta}')
            QMessageBox.information(
                self, 'geeSWAT — Completado',
                f'✔ El MDE se exportó a Google Drive:\n'
                f'  Carpeta: {GDRIVE_FOLDER}\n'
                f'  Archivo: {ruta}.tif\n\n'
                '1. Abre https://drive.google.com\n'
                f'2. Ve a la carpeta "{GDRIVE_FOLDER}"\n'
                '3. Descarga el archivo .tif\n'
                '4. Arrástralo al proyecto QGIS')
        else:
            self._log(f'✔ MDE guardado localmente: {ruta}')
            # Cargar en QGIS
            try:
                dem_info = GEE_DEMS[self._cmb_dem.currentIndex()]
                nombre   = dem_info['label'].split('—')[0].strip()
                rlayer   = QgsRasterLayer(ruta, f'MDE — {nombre}')
                if rlayer.isValid():
                    QgsProject.instance().addMapLayer(rlayer)
                    self._log('✔ Raster cargado en QGIS.')
                else:
                    self._log(f'⚠ Raster guardado pero no pudo cargarse automáticamente:\n  {ruta}')
            except Exception as ex:
                self._log(f'⚠ Error al cargar en QGIS: {ex}')

    def _descarga_error(self, msg):
        self._log(f'✖ ERROR: {msg}')
        QMessageBox.critical(self, 'geeSWAT — Error de descarga', f'{msg}')

    def _worker_terminado(self):
        self._btn_descargar.setEnabled(True)
        self._btn_cancelar_mde.setEnabled(False)

    # ══════════════════════════════════════════════════════════════════════════
    # HELPER VISUAL
    # ══════════════════════════════════════════════════════════════════════════
    def _generar_icono_cuenca(self, size):
        pm = QPixmap(size, size); pm.fill(Qt.transparent)
        p  = QPainter(pm); p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QBrush(QColor('#1565C0'))); p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, size - 8, size - 8)
        cx = size // 2; cy = size // 2; r = size // 5
        p.setBrush(QBrush(QColor('white')))
        path = QPainterPath()
        path.moveTo(cx, cy - r * 2)
        path.quadTo(cx + r * 2, cy, cx + r, cy + r)
        path.quadTo(cx, cy + r * 3, cx - r, cy + r)
        path.quadTo(cx - r * 2, cy, cx, cy - r * 2)
        p.drawPath(path)
        p.end(); return pm

    def closeEvent(self, event):
        if self._rubber:
            self._rubber.reset()
        if self._worker and self._worker.isRunning():
            self._worker.cancelar()
            self._worker.wait(3000)
        super().closeEvent(event)
