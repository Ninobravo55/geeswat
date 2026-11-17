# -*- coding: utf-8 -*-
"""
hidrologia_reporte.py — geeSWAT
Ventana de resultados independiente para el análisis hidrológico. Se abre
aparte del diálogo principal (no modal) y permite exportar el informe
completo (con fórmulas y curva hipsométrica) a una carpeta como HTML.

El SVG de la curva hipsométrica no se pre-visualiza dentro de la ventana
porque QTextBrowser (motor de texto enriquecido de Qt) no renderiza <svg>
inline de forma confiable — por eso el botón "Abrir informe" delega en el
navegador del sistema, igual que hacen otros plugins de QGIS con reportes
HTML (p.ej. ArcGeek Calculator).
"""

import os
import re
import datetime

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QFileDialog, QMessageBox
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QDesktopServices, QFont
from qgis.PyQt.QtCore import QUrl


def _quitar_svg_para_preview(html):
    """Vista previa dentro de Qt: reemplaza el <svg>...</svg> por una nota,
    porque QTextBrowser no lo renderiza. El archivo exportado sí conserva el SVG."""
    return re.sub(
        r'<svg.*?</svg>', '<p><i>(la curva hipsométrica se ve en el informe '
        'HTML exportado — ábrelo con el botón de abajo)</i></p>',
        html, flags=re.DOTALL)


class ResultadosHidrologiaDialog(QDialog):
    def __init__(self, html_completo, nombre_cuenca, carpeta_sugerida='', parent=None):
        super().__init__(parent)
        self.html_completo = html_completo
        self.nombre_cuenca = nombre_cuenca or 'cuenca'
        self.carpeta_sugerida = carpeta_sugerida
        self.ultima_ruta_exportada = None

        self.setWindowTitle(f'geeSWAT — Resultado del análisis: {self.nombre_cuenca}')
        self.setMinimumSize(760, 640)
        self.setWindowFlag(Qt.Window, True)

        vl = QVBoxLayout(self)
        vl.setContentsMargins(16, 16, 16, 16)
        vl.setSpacing(10)

        lbl = QLabel(f'Interpretación hidrológica — {self.nombre_cuenca}')
        lbl.setFont(QFont('Arial', 13, QFont.Bold))
        lbl.setStyleSheet('color:#0D47A1;')
        vl.addWidget(lbl)

        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)
        self._browser.setHtml(_quitar_svg_para_preview(html_completo))
        vl.addWidget(self._browser, 1)

        hl = QHBoxLayout()
        btn_exportar = QPushButton('Exportar informe HTML a carpeta…')
        btn_exportar.setStyleSheet(
            'QPushButton{background:#1B5E20;color:white;border:none;'
            'border-radius:6px;padding:10px 18px;font-weight:bold;}'
            'QPushButton:hover{background:#2E7D32;}')
        btn_exportar.clicked.connect(self._exportar)
        hl.addWidget(btn_exportar)

        btn_abrir = QPushButton('Abrir último informe exportado')
        btn_abrir.setStyleSheet(
            'QPushButton{background:white;color:#37474F;border:1.5px solid #B0BEC5;'
            'border-radius:6px;padding:9px 16px;}'
            'QPushButton:hover{background:#ECEFF1;}')
        btn_abrir.clicked.connect(self._abrir_ultimo)
        hl.addWidget(btn_abrir)

        hl.addStretch()
        btn_cerrar = QPushButton('Cerrar')
        btn_cerrar.setStyleSheet(
            'QPushButton{background:white;color:#37474F;border:1.5px solid #B0BEC5;'
            'border-radius:6px;padding:9px 16px;}'
            'QPushButton:hover{background:#ECEFF1;}')
        btn_cerrar.clicked.connect(self.close)
        hl.addWidget(btn_cerrar)

        vl.addLayout(hl)

    def _nombre_archivo_sugerido(self):
        ts = datetime.datetime.now().strftime('%Y%m%d_%H%M')
        seguro = re.sub(r'[^\w\-]+', '_', self.nombre_cuenca)
        return f'informe_hidrologico_{seguro}_{ts}.html'

    def _exportar(self):
        carpeta = QFileDialog.getExistingDirectory(
            self, 'Selecciona la carpeta de salida', self.carpeta_sugerida)
        if not carpeta:
            return
        ruta = os.path.join(carpeta, self._nombre_archivo_sugerido())
        try:
            with open(ruta, 'w', encoding='utf-8') as f:
                f.write(self.html_completo)
        except Exception as e:
            QMessageBox.critical(self, 'geeSWAT — Error',
                                  f'No se pudo escribir el archivo:\n{e}')
            return
        self.ultima_ruta_exportada = ruta
        QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))
        QMessageBox.information(self, 'geeSWAT',
                                 f'Informe exportado y abierto:\n{ruta}')

    def _abrir_ultimo(self):
        if not self.ultima_ruta_exportada or not os.path.isfile(self.ultima_ruta_exportada):
            QMessageBox.information(self, 'geeSWAT',
                                     'Todavía no se ha exportado ningún informe en esta sesión.')
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(self.ultima_ruta_exportada))
