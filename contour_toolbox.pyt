# -*- coding: utf-8 -*-
"""
=================================================
Tool Name: Contour Toolbox
Source: contour_toolbox.pyt
Author: Sian Bryson
Organization: OMAFA Environmental Management Branch
Date: 2026-06-16
ArcGIS Version: 3.5.4

Description: 
    Generates and cleans contours from a DTM image. Moves across a gridded study area to generate contours for each cell, then merges them back together.

Parameters:
    workspace:                  Geodatabase/workspace           Input       The geodatabase workspace
    imagery:                    Raster layer OR mosaic layer    Input       The source DTM imagery
    working_area:               Feature Layer (polygon)         Input       The study area grid
    breaklines:                 Feature layer OR raster layer   Input       Hydro breaklines
    dangle_threshold:           Integer                         Input       Maximum length of dangles to be removed (m)
    loop_threshold:             Integer                         Input       Maximum length of loops to be removed (m)
    smoothing_threshold:        Integer                         Input       Smoothing tolerance
    simplification_threshold:   Integer                         Input       Simplification tolerance
    output_fc:                  Feature Class                   Output      Output location for the final smoothed contours

Usage:
    Intended for use with large study areas where running the ArcGIS Pro Contour tool on the whole area would be prohibitively demanding.
=================================================
"""

import arcpy
import os
import uuid
import sys
import importlib

toolbox_dir = os.path.dirname(os.path.abspath(__file__))

if toolbox_dir not in sys.path:
    sys.path.insert(0, toolbox_dir)

from contour_tools.utils import purger
import contour_tools.imagery
importlib.reload(contour_tools.imagery)
from contour_tools.imagery import process_imagery
import contour_tools.cleanup
importlib.reload(contour_tools.cleanup)
from contour_tools.cleanup import clean_contours


crs = 'PROJCS["NAD_1983_CSRS_Ontario_MNR_Lambert",GEOGCS["GCS_North_American_1983_CSRS",DATUM["D_North_American_1983_CSRS",SPHEROID["GRS_1980",6378137.0,298.257222101]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Lambert_Conformal_Conic"],PARAMETER["False_Easting",930000.0],PARAMETER["False_Northing",6430000.0],PARAMETER["Central_Meridian",-85.0],PARAMETER["Standard_Parallel_1",44.5],PARAMETER["Standard_Parallel_2",53.5],PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]'

class Toolbox:
    def __init__(self):
        print("Toolbox instantiated")
        """Define the toolbox (the name of the toolbox is the name of the
        .pyt file)."""
        self.label = "Toolbox"
        self.alias = "toolbox"
        # List of tool classes associated with this toolbox
        self.tools = [ProcessImagery, CleanContours, RunFullWorkflow, CleanSmallPieces]

class ProcessImagery(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "1. Prepare DTM Imagery"
        self.description = "Creates a raster mask for the processing extent."

    def getParameterInfo(self):
        """Define the tool parameters."""

        workspace = arcpy.Parameter(
            displayName = "Workspace", 
            name = "workspace", 
            datatype = "DEWorkspace", 
            parameterType =  "Required",
            direction = "Input")
        
        imagery = arcpy.Parameter(
            displayName = "DTM LiDAR Imagery",
            name = "imagery",
            datatype = ["GPRasterLayer", "GPMosaicLayer"],
            parameterType = "Required",
            direction = "Input")
        
        working_area = arcpy.Parameter(
            displayName = "Processing area grid", 
            name = "working_area", 
            datatype = "GPFeatureLayer", 
            parameterType =  "Required",
            direction = "Input"  
        )
        working_area.filter.list = ["Polygon"]

        breaklines = arcpy.Parameter(
            displayName = "Waterbody breaklines", 
            name = "breaklines", 
            datatype = ["GPFeatureLayer", "GPRasterLayer"], 
            parameterType =  "Required",
            direction = "Input"  
        )

        output_path = arcpy.Parameter(
            displayName = "Output contours",
            name = "output_path",
            datatype = "DERasterDataset",
            parameterType = "Required",
            direction = "Output"
        )

        crs = arcpy.Parameter(
            displayName = "Coordinate system",
            name = "crs",
            datatype = "GPSpatialReference",
            parameterType = "Required",
            direction = "Input"
        )

        params = [workspace, imagery, working_area, breaklines, output_path, crs]
        return params

    def updateMessages(self, parameters):
        """Modify the messages created by internal validation for each tool
        parameter. This method is called after internal validation."""
        breaklines = parameters[3]

        if breaklines.value:
            desc = arcpy.Describe(breaklines.value)

            if desc.dataType in ["FeatureLayer", "FeatureClass"]:
                if desc.shapeType != "Polygon":
                    breaklines.setErrorMessage("Breaklines must either be a raster layer or polygon feature.")
        return
    def execute(self, parameters, messages):
        """The source code of the tool."""
        
        #Envrionment settings ---
        arcpy.env.overwriteOutput = True
        arcpy.env.addOutputsToMap = False
        arcpy.CheckOutExtension("Spatial")

        #Retrieve parameters ---
        my_workspace = parameters[0].valueAsText
        imagery = parameters[1].valueAsText
        working_area = parameters[2].valueAsText
        breaklines = parameters[3].valueAsText
        output_path = parameters[4].valueAsText
        erie_crs = parameters[5].valueAsText

        process_imagery(my_workspace, imagery, working_area, breaklines, output_path, erie_crs)
    

class CleanContours(object):
    def __init__(self):
        """Define the tool (tool name is the name of the class)."""
        self.label = "2. Clean Contours"
        self.description = "Removes topographic artifacts from the input contours (loops, knots, dangles) and reconnects line segments."

    def getParameterInfo(self):
        """Define the tool parameters."""

        in_fc = arcpy.Parameter(
            displayName = "Contours to be cleaned", 
            name = "in_fc", 
            datatype = "GPFeatureLayer", 
            parameterType =  "Required",
            direction = "Input")

        loop_threshold = arcpy.Parameter(
            displayName = "Maximum length of islands to be removed", 
            name = "loop_threshold", 
            datatype = "GPLong", 
            parameterType =  "Required",
            direction = "Input")
        loop_threshold.value = 100
        
        working_area = arcpy.Parameter(
            displayName = "Area of interest boundary", 
            name = "working_area", 
            datatype = "GPFeatureLayer", 
            parameterType =  "Required",
            direction = "Input"  
        )

        out_fc = arcpy.Parameter(
            displayName = "Outfile location", 
            name = "out_file", 
            datatype = "DEFeatureClass", 
            parameterType =  "Required",
            direction = "Output"  
        )

        params = [in_fc, loop_threshold, working_area, out_fc]
        return params

    def execute(self, parameters, messages):
        """The source code of the tool."""
        
        #Environment settings ---
        arcpy.CheckOutExtension("Spatial")
        arcpy.env.overwriteOutput = True
        arcpy.env.addOutputsToMap = False
        
        #Retrieve parameters ---
        in_fc = parameters[0].valueAsText
        loop_threshold = parameters[1].value
        working_area = parameters[2].valueAsText
        out_fc = parameters[3].valueAsText
        
        clean_contours(in_fc, working_area, loop_threshold, out_fc)


class RunFullWorkflow(object):
    def __init__(self):
        self.label = "0. Run Full Workflow"
        self.description = "Executes all tools in sequence and adds simplification/smoothing."

    def getParameterInfo(self):
        workspace = arcpy.Parameter(
            displayName = "Workspace",
            name = "workspace",
            datatype = "DEWorkspace",
            parameterType = "Required",
            direction = "Input"
        )
    
        imagery = arcpy.Parameter(
            displayName="DTM LiDAR Imagery",
            name="imagery",
            datatype = ["GPRasterLayer", "GPMosaicLayer"],
            parameterType="Required",
            direction="Input")

        working_area = arcpy.Parameter(
            displayName="Processing Area",
            name="working_area",
            datatype="GPFeatureLayer",
            parameterType="Required",
            direction="Input")

        breaklines = arcpy.Parameter(
            displayName="Breaklines",
            name="breaklines",
            datatype=["GPFeatureLayer", "GPRasterLayer"],
            parameterType="Required",
            direction="Input")

        loop_threshold = arcpy.Parameter(
            displayName = "Maximum length of islands to be removed", 
            name = "loop_threshold", 
            datatype = "GPLong", 
            parameterType =  "Required",
            direction = "Input")
        loop_threshold.value = 100
        
        simplification_threshold = arcpy.Parameter(
            displayName = "Simplification tolerance (map units)", 
            name = "simplification_threshold", 
            datatype = "GPLong", 
            parameterType =  "Required",
            direction = "Input")
        simplification_threshold.value = 1

        output_fc = arcpy.Parameter(
            displayName="Final Output Contours",
            name="output_fc",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Output")
        
        crs = arcpy.Parameter(
            displayName = "Coordinate system",
            name = "crs",
            datatype = "GPSpatialReference",
            parameterType = "Required",
            direction = "Input"
        )

        return [workspace, imagery, working_area, breaklines, loop_threshold, simplification_threshold, output_fc, crs]
    
    def execute(self, parameters, messages):

        #Environment settings ---
        arcpy.env.overwriteOutput = True
        arcpy.env.addOutputsToMap = False
        arcpy.env.parallelProcessingFactor = "90%"
        arcpy.CheckOutExtension("Spatial")
        
        #Retrieve parameters ---
        workspace = parameters[0].valueAsText
        imagery = parameters[1].valueAsText
        working_area = parameters[2].valueAsText
        breaklines = parameters[3].valueAsText
        loop_threshold = parameters[4].value
        simplification_threshold = parameters[5].value
        output_fc = parameters[6].valueAsText
        crs = parameters[7].valueAsText
        uid = str(uuid.uuid4())[:6]
        
        #Temp file paths ---
        temp_raster = os.path.join(workspace, f"processed_dtm_{uid}")
        temp_contours = os.path.join(workspace, f"raw_contours_{uid}")
        clipped_contours = rf"memory\clipped_contours_{uid}"
        temp_clean = os.path.join(workspace, f"clean_contours_{uid}")
        temp_simple = os.path.join(workspace, f"simple_contours_{uid}")

        try:
            #1. Process imagery ---
            import time
            arcpy.AddMessage("Step 1. Processing imagery ---")
            start = time.time()
            processed_raster = process_imagery(workspace, imagery, working_area, breaklines, temp_raster, crs)
            end = time.time() - start
            hours, remainder = divmod(end, 3600)
            minutes, seconds = divmod(remainder, 60)
            arcpy.AddMessage(f"Processed imagery ({int(hours)}H {int(minutes)}M)")
        
            #2. Create contours ---
            arcpy.AddMessage("Step 2. Creating contours ---")
            start = time.time()
            arcpy.env.extent = working_area
            raw_contours = arcpy.sa.Contour(processed_raster, temp_contours,  0.5)
            end = time.time() - start
            hours, remainder = divmod(end, 3600)
            minutes, seconds = divmod(remainder, 60)
            arcpy.AddMessage(f"Generated contours ({int(hours)}H {int(minutes)}M)")
            arcpy.AddMessage("Clipping contours to study area...")
            arcpy.analysis.PairwiseClip(raw_contours, working_area, clipped_contours)

            #3. Clean contours ---
            arcpy.AddMessage("Step 3. Cleaning contours ---")
            start = time.time()
            clean_contours(clipped_contours, working_area, loop_threshold, output_fc)
            arcpy.SetParameterAsText(6, output_fc)
            end = time.time() - start
            hours, remainder = divmod(end, 3600)
            minutes, seconds = divmod(remainder, 60)
            arcpy.AddMessage(f"Cleaned contours ({int(hours)}H {int(minutes)}M)")
            # #4. Final processing ---
            # arcpy.AddMessage("Step 4. Smoothing and simplifying ---")
            # arcpy.cartography.SimplifyLine(
            #     in_features = clean_contours_fc,
            #     out_feature_class = temp_simple,
            #     algorithm = "POINT_REMOVE",
            #     tolerance = simplification_threshold,
            #     collapsed_point_option = "NO_KEEP"
            # )
            # arcpy.cartography.SmoothLine(
            #     in_features= temp_simple,
            #     out_feature_class = output_fc,
            #     algorithm = "PAEK",
            #     tolerance = smoothing_threshold
            # )
        finally:
            arcpy.CheckInExtension("Spatial")
            # purger([temp_raster, temp_contours, temp_clean, temp_simple, clipped_contours])

class CleanSmallPieces(object):
    def __init__(self):
        self.label = "Extra: Clean small pieces"
        self.description = "Executes all tools in sequence and adds simplification/smoothing."

    def getParameterInfo(self):

        in_fc = arcpy.Parameter(
            displayName="Input fc",
            name="in_fc",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Input")
        
        out_fc = arcpy.Parameter(
            displayName="Final Output Contours",
            name="out_fc",
            datatype="DEFeatureClass",
            parameterType="Required",
            direction="Output")

        return [in_fc, out_fc]
    
    def execute(self, parameters, messages):

        #Environment settings ---
        arcpy.env.overwriteOutput = True
        arcpy.env.addOutputsToMap = False
        arcpy.CheckOutExtension("Spatial")
        from contour_tools.cleanup import remove_duplicate_pieces
        #Retrieve parameters ---
        in_fc = parameters[0].valueAsText
        out_fc = parameters[1].valueAsText
        uid = str(uuid.uuid4())[:6]
        
        try:
            #1. Process imagery ---
            arcpy.management.CopyFeatures(in_fc, out_fc)
            remove_duplicate_pieces(out_fc, uid)
            arcpy.SetParameterAsText(1, out_fc)
           
        finally:
            arcpy.CheckInExtension("Spatial")