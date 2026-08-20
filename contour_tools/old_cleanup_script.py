"""
cleanup.py

Contour cleaning routines used by the Contour Toolbox.

clean_contours() loops through the remove_artifacts() function until there are either 
no more artifacts to remove, or a threshold number of iterations is reached.

----------------------------------------------------------------------------------------
1.  The endpoints of the contour lines are extracted and their connectivity is documented.
    The connectivity (c) of a line's endpoints (p1, p2) is defined as the number of other endpoints each endpont overlaps with.
        >   i.e., a segment sandwiched between two other segments would have connectivity values of c1 = 1, c2 = 1. 
            A loop would also have c1 = 1, c2 =1. A free-floating segment would have c1 = 0, c2 = 0.

2.  Candidate features for removal are compiled into a list by Object ID. These include:
        a)  Small loops:                    Features under a threshold length whose start and end points overlap)
        b)  Dangles:                        Only one endpoint has c = 0; the other is connected to at least one other line.        
        c)  Floating segments:              Neither endpoint is connected to other lines.
        d)  Duplicate segments:             Two or more non-loop segments share identical endpoints.
        e)  Four-way intersections loops:   A loop whose start/endpoint are also connected to at least two other points.

3.  Line segments that are touching the study area boundary are removed from the candidate list

4.  Remaining candidates are deleted

5.  A dissolve is performed on the contours to repair segmentation.
----------------------------------------------------------------------------------------
"""

import arcpy
import uuid
import os

from contour_tools.utils import (
    deleter,
    purger,
    is_closed_loop,
    rounded_xy,
    MAX_ITERATIONS
)

def reduce_noise(target_fc):
    """Removes small artifacts (line segments under 20 metres in length) from the contours."""
    deleted_loops_count = 0

    #Drop very small artifacts first
    temp_output = r"memory\filtered_contours"
    arcpy.management.CopyFeatures(target_fc, temp_output)
    with arcpy.da.UpdateCursor(temp_output, ["SHAPE@LENGTH"]) as cursor:
        for row in cursor:
            if row[0] < 20:
                cursor.deleteRow()
                deleted_loops_count += 1
    arcpy.AddMessage(f"Deleted {deleted_loops_count} very small pieces...")
    
    deleter(target_fc)
    arcpy.AddMessage("Initial dissolve...")
    arcpy.analysis.PairwiseDissolve(
        in_features = temp_output,
        out_feature_class = target_fc,
        dissolve_field = "Contour",
        multi_part = "SINGLE_PART"
    )
    return target_fc

def process_duplicate(oid, length, key, pair_best, max_length):
    if key not in pair_best:
        pair_best[key] = (oid, length)
        return None
    best_oid, best_length = pair_best[key]
    if length > max_length:
        return None
    if length< best_length:
        pair_best[key] = (oid, length)
        return best_oid
    return oid

def remove_artifacts(in_fc, dangle_threshold, loop_threshold, aoi):
    """Removes dangles, 3/4 way intersections, and redundant lines."""
    
    counts = {
        "small_loops" : 0,
        "dangles" : 0,
        "duplicates" : 0,
        "floating" : 0,
        "four_way_loops" : 0}

    uid = str(uuid.uuid4())[:8]
    
    endpoints_fc = rf"memory\line_endpoints_{uid}"
    spatial_join_fc = rf"memory\endpoint_counts_{uid}"
    working_layer = f"working_layer_{uid}"
    dissolved = rf"memory\dissolved_{uid}"
    
    out_fc = os.path.join(os.path.dirname(in_fc), f"cleaned_fc_{uid}")

    temp_items = [endpoints_fc, spatial_join_fc, dissolved, working_layer]
    
    try:
        # -- set up working copy
        arcpy.management.CopyFeatures(in_fc, out_fc)
    
        # -- build connectivity --    
    
        arcpy.management.FeatureVerticesToPoints(
            in_features = out_fc,
            out_feature_class = endpoints_fc,
            point_location = "BOTH_ENDS"
        )
    
        arcpy.AddMessage("Counting endpoint connections...")
        arcpy.analysis.SpatialJoin(
            target_features = endpoints_fc,
            join_features = endpoints_fc,
            out_feature_class = spatial_join_fc,
            join_operation = "JOIN_ONE_TO_ONE",
            join_type = "KEEP_COMMON",
            match_option = "INTERSECT"
        )
    
        # Get line lengths and loops --------------------------
        point_connectivity = {}
        with arcpy.da.SearchCursor(spatial_join_fc, ["SHAPE@XY", "Join_Count"]) as cursor:
            for xy, jc in cursor:
                coord = rounded_xy(arcpy.Point(*xy))
                point_connectivity[coord] = jc - 1 # <- removes self-count
        
        candidate_oids = set()
        pair_best = {}
        
        #-- scan features --
        MAX_DUP_LENGTH = min(dangle_threshold * 3, loop_threshold)
        with arcpy.da.SearchCursor(out_fc, ["OID@", "SHAPE@", "SHAPE@LENGTH"]) as cursor:
            for oid, geom, length in cursor:
    
                if not geom:
                    continue

                is_loop = is_closed_loop(geom)
                
                #1. Remove small loops
                if is_loop and length < loop_threshold:
                    candidate_oids.add(oid)
                    counts["small_loops"] += 1
                    continue
                        
                #2. Protect large loops
                if length > loop_threshold * 4:
                    continue
                
                # -- endpoints --
                p1 = rounded_xy(geom.firstPoint)
                p2 = rounded_xy(geom.lastPoint)
    
                c1 = point_connectivity.get(p1, 0)
                c2 = point_connectivity.get(p2, 0)
    
                dead_end1 = (c1 == 0)
                dead_end2 = (c2 == 0)
                
                #3. Remove dangles and floating segments
                if length < dangle_threshold:
                    
                    if dead_end1 != dead_end2:
                        candidate_oids.add(oid)
                        counts["dangles"] += 1
                        continue

                    if dead_end1 and dead_end2:
                        candidate_oids.add(oid)
                        counts["floating"] += 1
                        continue
                
                #4. Duplicate segments
                
                
                if not is_loop:
                    key = tuple(sorted([p1, p2]))
                    
                    duplicate_oid = process_duplicate(
                        oid = oid,
                        length = length,
                        key = key,
                        pair_best = pair_best,
                        max_length = MAX_DUP_LENGTH    
                    )
                    if duplicate_oid is not None:
                        candidate_oids.add(duplicate_oid)
                        counts["duplicates"] += 1
                    
                #4. Detect 4-way intersection loops
    
                if is_loop and c1 >= 3:
                        candidate_oids.add(oid)
                        counts["four_way_loops"] += 1
    
        arcpy.AddMessage(f"Candidate features identified for removal: {len(candidate_oids)}")
        arcpy.AddMessage("\n --- Artifact Summary ---")
        for k, v in counts.items():
            arcpy.AddMessage(f"{k}: {v}")
    
        #Delete features outside of AOI --------------------
        if not candidate_oids:
            arcpy.AddMessage(f"No artifacts left to remove. Nice!")
            return out_fc, 0
        
        arcpy.AddMessage(f"Evaluating {len(candidate_oids)} candidates...")
        deleter(working_layer)

        # -- Remove candidates ---
        arcpy.management.MakeFeatureLayer(out_fc, working_layer)
        oid_field = arcpy.Describe(working_layer).OIDFieldName
        oid_list_str = ",".join(map(str, candidate_oids))

        #Select candidates
        arcpy.management.SelectLayerByAttribute(
            in_layer_or_view = working_layer,
            selection_type = "NEW_SELECTION",
            where_clause = f"{oid_field} IN ({oid_list_str})"
        )

        init_count = int(arcpy.management.GetCount(working_layer)[0])
    
        #Remove candidates that are touching the AOI boundary
        arcpy.management.SelectLayerByLocation(
            in_layer = working_layer,
            overlap_type = "INTERSECT",
            select_features = aoi,
            selection_type = "REMOVE_FROM_SELECTION",
        )

        not_touching = int(arcpy.management.GetCount(working_layer)[0])
        arcpy.AddMessage(f"Candidates touching AOI boundary: {init_count - not_touching}")
    
        # -- remove candidates --
    
        selected = int(arcpy.management.GetCount(working_layer)[0])
        if selected:
            arcpy.management.DeleteFeatures(working_layer)
            arcpy.AddMessage(f"Deleted {selected} artifacts")
            arcpy.AddMessage("\nReconnecting broken contour segments...")
    
            arcpy.analysis.PairwiseDissolve(
                out_fc,
                dissolved,
                dissolve_field="Contour",
                multi_part="SINGLE_PART"
            )
            arcpy.AddMessage("Dissolve complete.")
            arcpy.management.Delete(out_fc)
            arcpy.management.CopyFeatures(dissolved, out_fc)
        else: arcpy.AddMessage("No artifacts met spatial selection criteria.")
        arcpy.AddMessage(f"Returning selected={selected}")
        return out_fc, selected

    finally:
        for item in temp_items:
            try: deleter(item)
            except Exception as e:
                arcpy.AddMessage(f"Warning: could not delete {item}: {e}")

def build_aoi_boundary(working_area):
    dissolved_grid = r"memory\dissolved_grid"
    boundary = r"memory\outer_boundary"
    purger([dissolved_grid, boundary])
    arcpy.management.Dissolve(working_area, dissolved_grid)
    arcpy.management.PolygonToLine(dissolved_grid, boundary)
    return boundary

def clean_contours(in_fc, working_area, dangle_threshold, loop_threshold, out_fc):
    """Executes the cleanup loop defined in remove_artifacts. Will stop looping if either the maximum number of iterations is reached or if no more features are deletion candidates."""

    arcpy.AddMessage("Starting artifact removal...")
    
    #Environment settings ---------------------
    arcpy.env.extent = None
    
    #File paths  --------------------------------

    arcpy.AddMessage("Refreshing copy of contour file...")
    deleter(out_fc)
    arcpy.management.CopyFeatures(in_fc, out_fc)
    
    #Build aoi ---------------------------
    aoi = build_aoi_boundary(working_area)

    #Clean up topology
    iteration = 1
    current_fc = reduce_noise(out_fc)
    previous_fc = None

    while iteration <= MAX_ITERATIONS:
        arcpy.AddMessage(f"\n-------------------")
        arcpy.AddMessage(f"--- Iteration {iteration} ---")
        arcpy.AddMessage(f"-------------------")

        #Diagnostics
        count = int(arcpy.management.GetCount(current_fc)[0])
        arcpy.AddMessage(f"Feature count: {count}")
        arcpy.AddMessage(f"Using FC: {current_fc}")
        
        #Run artifact cleaner -------------------------------
        new_fc, deleted = remove_artifacts(
            current_fc,
            dangle_threshold, 
            loop_threshold,
            aoi)
        arcpy.AddMessage(f"deleted={deleted}")
        if previous_fc and arcpy.Exists(previous_fc):
            try: 
                arcpy.management.Delete(previous_fc)
                arcpy.AddMessage(f"Deleted temp file: {previous_fc}")
            except Exception as e: 
                arcpy.AddMessage(f"Could not delete {previous_fc}: {e}")
        previous_fc = current_fc
        current_fc = new_fc

        #Exit conditions -------------
        if deleted == 0:
            arcpy.AddMessage("No changes made. Stopping cleanup.")
            break
        else: iteration += 1
    arcpy.management.Delete(out_fc)
    arcpy.management.CopyFeatures(current_fc, out_fc)

    if current_fc != out_fc and arcpy.Exists(current_fc):
        arcpy.management.Delete(current_fc)
        arcpy.AddMessage(f"Deleted temp file: {current_fc}")

    arcpy.AddMessage("Clean up complete.")
    return out_fc

def clean_contours(in_fc, working_area, dangle_threshold, loop_threshold, out_fc):
    """Executes the cleanup loop defined in remove_artifacts. Will stop looping if either the maximum number of iterations is reached or if no more features are deletion candidates."""

    arcpy.AddMessage("Starting artifact removal...")
    
    #Environment settings ---------------------
    arcpy.env.extent = None
    
    #File paths  --------------------------------

    arcpy.AddMessage("Refreshing copy of contour file...")
    deleter(out_fc)
    arcpy.management.CopyFeatures(in_fc, out_fc)
    
    #Build aoi ---------------------------
    aoi = build_aoi_boundary(working_area)

    #Clean up topology
    iteration = 1
    current_fc = reduce_noise(out_fc)
    previous_fc = None

    while iteration <= MAX_ITERATIONS:
        arcpy.AddMessage(f"\n-------------------")
        arcpy.AddMessage(f"--- Iteration {iteration} ---")
        arcpy.AddMessage(f"-------------------")

        #Diagnostics
        count = int(arcpy.management.GetCount(current_fc)[0])
        arcpy.AddMessage(f"Feature count: {count}")
        arcpy.AddMessage(f"Using FC: {current_fc}")
        
        #Run artifact cleaner -------------------------------
        new_fc, deleted = remove_artifacts(
            current_fc,
            dangle_threshold, 
            loop_threshold,
            aoi)
        arcpy.AddMessage(f"deleted={deleted}")
        if previous_fc and arcpy.Exists(previous_fc):
            try: 
                arcpy.management.Delete(previous_fc)
                arcpy.AddMessage(f"Deleted temp file: {previous_fc}")
            except Exception as e: 
                arcpy.AddMessage(f"Could not delete {previous_fc}: {e}")
        previous_fc = current_fc
        current_fc = new_fc

        #Exit conditions -------------
        if deleted == 0:
            arcpy.AddMessage("No changes made. Stopping cleanup.")
            break
        else: iteration += 1
    arcpy.management.Delete(out_fc)
    arcpy.management.CopyFeatures(current_fc, out_fc)

    if current_fc != out_fc and arcpy.Exists(current_fc):
        arcpy.management.Delete(current_fc)
        arcpy.AddMessage(f"Deleted temp file: {current_fc}")

    arcpy.AddMessage("Clean up complete.")
    return out_fc

