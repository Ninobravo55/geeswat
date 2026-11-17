"""
Export router mejorado — sube rasters + tablas de equivalencia/metadatos.

Cuando el área es grande, sube a Google Drive:
  ✓ Raster GeoTIFF
  ✓ Tabla de equivalencia (CSV, JSON)
  ✓ Metadatos (JSON con info del procesamiento)
"""

import os
import json
import shutil
from pathlib import Path


def upload_associated_files_to_drive(gcs_client, local_output_folder, drive_folder_id, feedback=None):
    """
    Sube archivos asociados al raster a Google Drive.
    
    Busca en la carpeta local:
      - *_lookup.csv, *_equivalence.csv (tablas)
      - *_metadata.json (información del procesamiento)
      - *.json (geojson, configuración)
    
    y los sube a Drive en la misma carpeta que el raster.
    """
    
    if not os.path.exists(local_output_folder):
        if feedback:
            feedback.pushWarning(f"Carpeta de salida no existe: {local_output_folder}")
        return []
    
    uploaded_files = []
    
    # Patrones de archivos a subir
    patterns = [
        '*_lookup.csv',
        '*_lookup.txt',
        '*_equivalence.csv',
        '*_metadata.json',
        '*.json',
    ]
    
    try:
        from google.colab import auth
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        
        drive_service = build('drive', 'v3')
        
        for pattern in patterns:
            for filepath in Path(local_output_folder).glob(pattern):
                if filepath.name.endswith('.tif') or filepath.name.endswith('.tiff'):
                    continue  # Skip rasters, ya están subidos
                
                if feedback:
                    feedback.pushInfo(f"Subiendo archivo asociado: {filepath.name}")
                
                try:
                    # Crear metadatos del archivo
                    file_metadata = {
                        'name': filepath.name,
                        'parents': [drive_folder_id]
                    }
                    
                    # Subir archivo
                    media = MediaFileUpload(str(filepath), resumable=True)
                    file_obj = drive_service.files().create(
                        body=file_metadata,
                        media_body=media,
                        fields='id, name'
                    ).execute()
                    
                    uploaded_files.append({
                        'name': filepath.name,
                        'drive_id': file_obj.get('id'),
                        'size': filepath.stat().st_size
                    })
                    
                    if feedback:
                        feedback.pushInfo(f"✓ {filepath.name} subido a Drive")
                
                except Exception as e:
                    if feedback:
                        feedback.pushWarning(f"Error subiendo {filepath.name}: {str(e)}")
        
    except ImportError:
        if feedback:
            feedback.pushWarning("Google Colab/Drive API no disponible en este contexto")
    except Exception as e:
        if feedback:
            feedback.pushWarning(f"Error en upload_associated_files: {str(e)}")
    
    return uploaded_files


def generate_metadata_json(algorithm_name, parameters, output_folder, feedback=None):
    """
    Genera un JSON con metadatos del procesamiento.
    
    Incluye:
      - Algoritmo usado
      - Parámetros de ejecución
      - Timestamp
      - Versión del plugin
      - CRS y extensión del AOI
    """
    import datetime
    
    metadata = {
        'algorithm': algorithm_name,
        'timestamp': datetime.datetime.now().isoformat(),
        'parameters': {
            k: str(v) for k, v in parameters.items() 
            if not k.startswith('__')  # Skip QGIS internals
        },
        'plugin_version': '2.0.8',
        'description': f'Metadata from GeoForest Tools {algorithm_name}'
    }
    
    # Guardar JSON
    json_path = os.path.join(output_folder, f'{algorithm_name}_metadata.json')
    try:
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2)
        
        if feedback:
            feedback.pushInfo(f"Metadatos generados: {json_path}")
        
        return json_path
    
    except Exception as e:
        if feedback:
            feedback.pushWarning(f"Error generando metadata: {str(e)}")
        return None


def export_with_metadata(output_folder, output_filename, algorithm_name, 
                        parameters, feedback=None):
    """
    Exporta raster + genera metadata automáticamente.
    
    Llamar en lugar de solo guardar el raster.
    """
    
    # Generar metadata
    metadata_path = generate_metadata_json(
        algorithm_name, 
        parameters, 
        output_folder, 
        feedback
    )
    
    # Retornar información de archivos generados
    files_generated = {
        'raster': os.path.join(output_folder, output_filename),
        'metadata': metadata_path
    }
    
    return files_generated


# Ejemplo de uso en un algoritmo:
# ──────────────────────────────────────────────────────────────────────────────
# En processAlgorithm():
#
#   # ... procesamiento normal ...
#
#   # Exportar con metadata
#   files = export_router_enhanced.export_with_metadata(
#       output_folder = out_folder,
#       output_filename = 'result.tif',
#       algorithm_name = 'GEOBOSQUES',
#       parameters = parameters,
#       feedback = feedback
#   )
#
#   # Si es muy grande, subir a Drive incluyendo tablas
#   if is_large_area:
#       uploaded = upload_associated_files_to_drive(
#           gcs_client, 
#           output_folder,
#           drive_folder_id,
#           feedback
#       )

