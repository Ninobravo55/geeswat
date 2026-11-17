# -*- coding: utf-8 -*-
import os
from qgis.core import QgsProcessingAlgorithm
from qgis.PyQt.QtGui import QIcon
from qgis.utils import iface

class HidrologiaAlg(QgsProcessingAlgorithm):
    def createInstance(self):
        return HidrologiaAlg()

    def name(self):
        return 'interpretacion_hidrologica'

    def displayName(self):
        return 'Interpretación Hidrológica (Explorador QSWAT)'

    def group(self):
        return 'Hidrología'

    def groupId(self):
        return 'hidrologia_geeswat'

    def icon(self):
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'Icons', 'dem.png')
        if os.path.exists(icon_path):
            return QIcon(icon_path)
        return QIcon()

    def shortHelpString(self):
        return "Lanza la herramienta interactiva de Interpretación Hidrológica."

    def initAlgorithm(self, config=None):
        pass

    def flags(self):
        return super().flags() | QgsProcessingAlgorithm.FlagNoThreading

    def processAlgorithm(self, parameters, context, feedback):
        from .hidrologia_dialog import HidrologiaHerramienta
        # Guardamos en self para evitar recolección de basura antes de que cierre
        self.dlg = HidrologiaHerramienta(iface, iface.mainWindow())
        self.dlg.show()
        return {}
