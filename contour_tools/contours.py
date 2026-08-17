"""
contours.py
Creates contours for the Contours Toolbox.
"""

import arcpy, os
from contour_tools.utils import deleter, BUFF

def create_contours(my_workspace, imagery, working_area, final_product):
    """Creates contours based on the processed imagery."""

    arcpy.AddMessage("Starting contour generation workflow...")
    
    #Environment settings ---------------------
    arcpy.env.workspace = my_workspace
    arcpy.env.extent = None
    arcpy.env.addOutputsToMap = False
    
    #Helper functions  --------------------------------
    temp_dir = arcpy.env.scratchGDB

    def setupWorkingArea(aoi):
        grid_data = {}
        fields = ["OID@", "SHAPE@"]

        with arcpy.da.SearchCursor(aoi, fields) as cursor:
            for row in cursor:
                grid_id = row[0]
                ext = row[1].extent 
                
                #Buffer the extent by 5 metres
                grid_data[grid_id] = {   
                    "xmin": ext.XMin - BUFF,
                    "ymin": ext.YMin - BUFF,
                    "xmax": ext.XMax + BUFF,
                    "ymax": ext.YMax + BUFF
                }
        return(grid_data)

    def contour(in_raster, grid_id, interval):
        outpath = os.path.join(temp_dir, f"tile_{grid_id}")
        deleter(outpath)
        
        try:
            arcpy.sa.Contour(
                in_raster = in_raster,
                out_polyline_features = outpath,
                contour_interval = interval
            )

        except Exception as e:
            arcpy.AddWarning(f"Contour failed for tile {grid_id}: {e}")
            return None
        
        return outpath

    # Set up fishnet working area 

    grid_dict = setupWorkingArea(working_area)
    contour_list  = []

    arcpy.AddMessage(f"Processing {len(grid_dict)} grid sections...")
    arcpy.SetProgressor("step", "Processing items...", 0, len(grid_dict), 1)

    for grid_id, coords in grid_dict.items():
        arcpy.SetProgressorLabel(f"Creating contours for grid section {grid_id} of {len(grid_dict)}")
        
        with arcpy.EnvManager(extent = arcpy.Extent(coords['xmin'], coords['ymin'], coords['xmax'], coords['ymax'])):
            out_layer = contour(imagery, grid_id, 0.5)
            if out_layer and arcpy.Exists(out_layer):
                contour_list.append(out_layer)
            else: arcpy.AddWarning(f"Contour failed for tile {grid_id}")

        arcpy.SetProgressorPosition()
    
    arcpy.ResetProgressor()    
    
    
    arcpy.AddMessage(f"Merging contour layers...")
    
    deleter(final_product)

    arcpy.management.Merge(inputs = contour_list, output = final_product)
    
    arcpy.AddMessage("Repairing geometry...")
    
    arcpy.management.RepairGeometry(in_features = final_product)

    arcpy.AddMessage("Cleaning up scratch files...")
    for temp_file in contour_list:
            deleter(temp_file)
        
    arcpy.AddMessage("Workflow complete.")
    return final_product