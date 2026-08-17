"""
imagery.py
Processes imagery for use in the Contour Toolbox.
"""

import arcpy, os
from contour_tools.utils import (deleter, purger)

def process_imagery(my_workspace, imagery, working_area, breaklines, output_path, crs):
    """Runs average focal statistics on the input imagery (5x5 window) and flattens the elevation of waterbodies to a local average using Zonal Statistics."""
    deleter("memory")
    arcpy.management.ClearWorkspaceCache()
    arcpy.env.extent = working_area
    
    with arcpy.EnvManager(workspace = my_workspace, overwriteOutput = True, outputCoordinateSystem = crs):
        
        # Set up parameters -------------------
        zonal_stats_outpath = os.path.join(my_workspace, "zonalStatistics")
        waterbody_raster_path = r"memory\hydro_breaklines_raster"
        #Clean up workspace
        purger([waterbody_raster_path, zonal_stats_outpath, output_path])
    
        imagery_raster = arcpy.Raster(imagery)
        desc_imagery = arcpy.Describe(imagery_raster)
        arcpy.env.snapRaster = imagery
        if desc_imagery.dataType not in ["MosaicLayer", "RasterLayer", "RasterDataset"]:
            raise arcpy.ExecuteError("Unsupported imagery type.")

        desc_breaklines = arcpy.Describe(breaklines)
        if desc_breaklines.dataType in ["FeatureLayer", "FeatureClass"]:
            if desc_breaklines.shapeType == "Polygon":
                arcpy.AddMessage("Converting waterbody polygons to raster...")
                arcpy.conversion.PolygonToRaster(breaklines,"OBJECTID",waterbody_raster_path)
                waterbodies = arcpy.sa.Raster(waterbody_raster_path)
            else: raise arcpy.ExecuteError("Breaklines must be polygon features.")
        else:
            waterbodies = arcpy.sa.Raster(breaklines)

        arcpy.AddMessage("Running focal statistics (mean, 5x5 window) on the imagery...")
        smoothed_imagery = arcpy.sa.FocalStatistics(
            in_raster= imagery_raster,
            neighborhood="Rectangle 5 5 CELL",
            statistics_type="MEAN",
        )
    
        arcpy.AddMessage("Determining average elevation of each water body...")
        zonal_raster = arcpy.sa.ZonalStatistics(
            in_zone_data = waterbodies,
            zone_field = "Value",
            in_value_raster = smoothed_imagery,
            statistics_type = "MEAN",
        )

        arcpy.AddMessage("Applying water body elevation to original imagery...")
        output_raster_calc = arcpy.sa.Con(
            arcpy.sa.IsNull(zonal_raster),
            smoothed_imagery, 
            zonal_raster)
        output_raster_calc.save(output_path)
        
    deleter(waterbody_raster_path)
    deleter("memory")
    arcpy.management.ClearWorkspaceCache()
    arcpy.AddMessage("Finished.")
    return output_path
