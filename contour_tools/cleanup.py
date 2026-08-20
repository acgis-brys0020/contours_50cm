"""
cleanup.py

Contour cleaning routines used by the Contour Toolbox.

----------------------------------------------------------------------------------------
1.  Candidate features for removal are compiled into a list by Object ID. These include:
        a)  Small loops:            Features under a threshold length whose start and end points overlap.
        b)  Fragments:              Any piece under 10 metres.   
        c)  Floating segments:      Neither endpoint of the segment is connected to another lines or touching the AOI.

2.  In each segment, the coordinates of each point are evaluated. If two points share the same coordinates and the shape is NOT a loop, the segment is inspected further.
    3.1.    Segments with possible self-overlaps are dissolved and the geometry is scanned for knots, fragments, and duplicate segments.
    3.2.    Artifacts are removed and the segment is dissolved back together. Cleaned segments replace the originals.

3.  Candidates found in step 1 are deleted.
----------------------------------------------------------------------------------------
"""

#Imports ---------------------
import arcpy
import uuid
import os
from collections import defaultdict
import time
from contour_tools.utils import (
    deleter,
    purger,
    rounded_xy,
    get_points,
    is_closed_loop,
)
#Constants -------------------
MIN_FRAGMENT_LENGTH = 10
MAX_DUPLICATE_GAP = 20
MAX_KNOT_LENGTH = 30
MAX_KNOT_CONNECTIONS = 3

def make_layer(fc, id_list, oid_fields, layer_name, in_not_in):
    """Creates a feature layer with a select list of objects."""
    oid_string = ",".join(map(str, id_list))
    layer = arcpy.management.MakeFeatureLayer(
            fc,
            f"{layer_name}",
            f"{oid_fields} {in_not_in} ({oid_string})"
        )
    return layer

def build_aoi_boundary(working_area):
    """Creates the boundary used to determine if features are touching it."""
    dissolved_grid = r"memory\dissolved_grid"
    boundary = r"memory\outer_boundary"
    purger([dissolved_grid, boundary])
    arcpy.management.Dissolve(working_area, dissolved_grid)
    arcpy.management.PolygonToLine(dissolved_grid, boundary)
    return boundary

def touching_boundary(out_fc, working_layer, aoi):
    """Determines if features are touching the boundary."""
    deleter(working_layer)
    touching_boundaries = set()
    oid_field = arcpy.Describe(out_fc).OIDFieldName

    arcpy.management.MakeFeatureLayer(out_fc, working_layer)

    #Uses within a distance instead of intersect since it's more forgiving.
    arcpy.management.SelectLayerByLocation(
        in_layer = working_layer,
        overlap_type = "WITHIN_A_DISTANCE",
        select_features = aoi,
        search_distance = 0.5,
        selection_type = "NEW_SELECTION",
    )
    with arcpy.da.SearchCursor(working_layer, [oid_field]) as cursor:
        for row in cursor:
            touching_boundaries.add(row[0])
    return touching_boundaries
    
def detect_suspect_geometry(geom, min_sep = 5):
    """Looks for vertices in the same feature that share the same coordinates, which suggests a self-intersection."""
    vertices = [
        rounded_xy(point)
        for part in geom
        for point in part
        if point
    ]

    if len(vertices) < 3:
        return []

    seen = {}
    duplicates = []
    start_vertex = vertices[0]

    for i, coord in enumerate(vertices):

        # Ignore normal closed-loop closure
        if (coord == start_vertex and i == len(vertices) - 1):
            continue

        first_idx = seen.get(coord)
        if first_idx is not None:
            if i - first_idx >= min_sep:
                duplicates.append((coord, first_idx, i)) 
        else:
            seen[coord] = i

    return duplicates

def is_floating_segment(geom, endpoint_counts):
    """Determines if a feature is "floating" and is a stray line not touching another feature. This excludes loops."""
    if is_closed_loop(geom):
        return False
    p1, p2 = get_points(geom)
    c1 = endpoint_counts.get(p1, 0) - 1
    c2 = endpoint_counts.get(p2, 0) -1
    return c1 == 0 and c2 == 0
   
def build_endpoint_counts(fc):
    endpoint_counts = defaultdict(int)
    with arcpy.da.SearchCursor(fc, ["SHAPE@"]) as cursor:
        for (geom,) in cursor:
            if not geom:
                continue
            p1, p2 = get_points(geom)

            endpoint_counts[p1] += 1
            endpoint_counts[p2] += 1

    return endpoint_counts

def detect_knots(out_fc, suspect_artifact_ids, uid, dissolved_fc, out_fc_oid_field):
    
    knot_fc = rf"memory\knot_fc_{uid}"

    #Returns an empty set an exits the function if no suspect features were found.
    if not suspect_artifact_ids:
        arcpy.AddMessage("No knot candidates found.")
        return set(), None
    
    #Creates a feature class containing only the suspect features.
    knot_layer = make_layer(out_fc, suspect_artifact_ids, out_fc_oid_field, "knot_layer", "IN")
    arcpy.management.CopyFeatures(knot_layer,knot_fc)

    #Dissolves the suspect features by ORIG_ID and by contour elevation. This will break up wonky geometry into independent features that can be removed.
    arcpy.AddMessage(f"Dissolving {arcpy.management.GetCount(knot_fc)} suspect features for cleaning...")
    start_time = time.time()
    arcpy.management.Dissolve(
        knot_fc,
        dissolved_fc,
        ["ORIG_ID", "Contour"],
        multi_part = "SINGLE_PART"
    )
    end_time = time.time() - start_time
    arcpy.AddMessage(f"Dissolved features into {arcpy.management.GetCount(dissolved_fc)} features ({end_time})")

    #Performs a spatial join on the dissolved features to determine their connectivity. This is THE most expensive and time consuming section of the code.
    arcpy.AddMessage(f"Performing spatial join...")
    joined_fc = rf"memory\joined_fc_{uid}"
    start_time = time.time()
    arcpy.analysis.SpatialJoin(
        target_features = dissolved_fc, 
        join_features = dissolved_fc,
        out_feature_class = joined_fc,
        join_operation = "JOIN_ONE_TO_ONE",
        join_type = "KEEP_ALL",
        match_option = "BOUNDARY_TOUCHES")
    end_time = time.time() - start_time
    arcpy.AddMessage(f"Spatial join complete ({end_time})")

    #Isolates features that are under a certain length and connected to fewer than two features, which suggests a knot.
    arcpy.AddMessage("Searching for removal candidates...")
    removal_candidates = set()
    with arcpy.da.SearchCursor(joined_fc, ["OID@", "SHAPE@LENGTH", "Join_Count"]) as cursor:
        for oid, length, join_count in cursor:
            if length < MAX_KNOT_LENGTH and join_count <= MAX_KNOT_CONNECTIONS:
                removal_candidates.add(oid)
    arcpy.AddMessage(f"Found {len(removal_candidates)} artifacts for removal.")
    return removal_candidates, dissolved_fc

def remove_fragments(dissolved_fc, complex_orig_ids, counts):
    """Removes very small pieces that may be created by the dissolve."""
    arcpy.AddMessage("Removing fragments from the isolated features...")
    FINAL_FRAGMENT_THRESHOLD = 10
    small_fragments = set()
    with arcpy.da.SearchCursor(dissolved_fc, ["OID@", "ORIG_ID", "SHAPE@LENGTH"]) as cursor:
        for oid, orig_id, length in cursor:
            if orig_id in complex_orig_ids:
                continue
            if length < FINAL_FRAGMENT_THRESHOLD:
                small_fragments.add(oid)
                counts["fragments"] += 1
    if small_fragments:
        dissolved_oid_field = arcpy.Describe(dissolved_fc).OIDFieldName
        tiny_fragments = make_layer(dissolved_fc, small_fragments, dissolved_oid_field, "tiny_fragments", "IN")
        arcpy.management.DeleteFeatures(tiny_fragments)
        return len(small_fragments)

def remove_duplicate_pieces(dissolved_fc, uid):
    """Removes a specific type of artifact, which becomes more common when generating contours for larger areas."""
    #Build groups
    groups = defaultdict(list)
    with arcpy.da.SearchCursor(dissolved_fc, ["OID@", "ORIG_ID", "SHAPE@", "SHAPE@LENGTH"]) as cursor:
        for oid, orig_id, geom, length in cursor:
            p1, p2 = get_points(geom)
            key = tuple(sorted([p1, p2]))
            groups[key].append((oid, orig_id, length))

    duplicate_group_count = defaultdict(int)
    duplicate_fragment_oids = set()

    for key, group in groups.items():

        if len(group) > 1:
            orig_ids = {x[1] for x in group}

            for orig_id in orig_ids:
                duplicate_group_count[orig_id] += 1
    complex_orig_ids = {
        orig_id for orig_id, count in duplicate_group_count.items()
        if count > 3
    }
    for orig_id in complex_orig_ids:
        arcpy.AddMessage(f"COMPLEX: ORIG_ID = {orig_id},"
                            f"groups = {duplicate_group_count[orig_id]}")

    for key, group in groups.items():
        orig_ids = {x[1] for x in group}
        if len(group) < 2:
            continue
    
        if any(orig_id in complex_orig_ids for orig_id in orig_ids):
            continue
        group.sort(key = lambda x: x[2])
        smallest = group[0][2]
        largest = group[-1][2]
        if largest / smallest > 50:
            #A few very small pieces and one large pieces, indicates artifacts are breaking up a loop
            keep_oids = {
                group[0][0], group[-1][0]
            }
            #Group is entirely small artifacts, only keep the smallest piece
        else: 
            keep_oids = {group[0][0]}

        for oid, _, _ in group:
            if oid not in (keep_oids):
                duplicate_fragment_oids.add(oid)

    if duplicate_fragment_oids:
        dissolved_oid_field = arcpy.Describe(dissolved_fc).OIDFieldName
        duplicate_fragments = make_layer(dissolved_fc, duplicate_fragment_oids, dissolved_oid_field, "duplicate_fragments", "IN")
        arcpy.management.DeleteFeatures(duplicate_fragments)

        temp_fc = rf"memory\temp_fc_{uid}"
        arcpy.management.Dissolve(
            dissolved_fc,
            temp_fc,
            ["ORIG_ID", "Contour"],
            multi_part = "SINGLE_PART")
        dup_count = int(arcpy.management.GetCount(duplicate_fragments)[0])
        deleter(dissolved_fc)
        arcpy.management.CopyFeatures(temp_fc, dissolved_fc)
    return complex_orig_ids, dup_count

def replace_suspect_geometry(out_fc, suspect_artifact_ids, dissolved_fc, artifact_candidates, uid, counts, out_fc_oid_field):
    """After being cleaned and repaired, the suspect contours replace the originals."""
    if dissolved_fc is not None:
        counts["other_artifacts"] = len(artifact_candidates)
        no_artifacts = rf"memory\no_artifacts_{uid}"

        if artifact_candidates:
            dissolved_oid_field = arcpy.Describe(dissolved_fc).OIDFieldName
            target_layer = make_layer(dissolved_fc, artifact_candidates, dissolved_oid_field, "target_layer", "NOT IN")
            arcpy.management.CopyFeatures(target_layer, no_artifacts)
        else:
            arcpy.AddMessage("No knot fragments identified.")
            arcpy.management.CopyFeatures(dissolved_fc, no_artifacts)
        deleter(dissolved_fc)

        arcpy.management.Dissolve(
            no_artifacts,
            dissolved_fc,
            ["ORIG_ID", "Contour"],
            multi_part = "SINGLE_PART")

        complex_orig_ids, dup_count = remove_duplicate_pieces(dissolved_fc, uid)
        frag_count = remove_fragments(dissolved_fc, complex_orig_ids, counts)
        repair_layer = make_layer(out_fc, suspect_artifact_ids, out_fc_oid_field, "repair_layer", "IN")
        arcpy.management.DeleteFeatures(repair_layer)
        arcpy.management.Append(dissolved_fc, out_fc, "NO_TEST")
        arcpy.AddMessage(f"Removed {dup_count} duplicates and {frag_count} fragments.")
        return out_fc
    return out_fc

def scan_features(fc, results, loop_threshold):
    arcpy.AddMessage("Creating connectivity...") 
    endpoint_counts = build_endpoint_counts(fc)
    t0 = time.time()
    with arcpy.da.SearchCursor(fc, ["OID@", "SHAPE@", "SHAPE@LENGTH"]) as cursor:
        for oid, geom, length in cursor:
            
            if not geom:
                continue
            
            #1. Remove all tiny pieces
            if length < MIN_FRAGMENT_LENGTH:
                results.candidate_oids.add(oid)
                results.fragments += 1
                continue
                
            #2. Remove small loops
            is_loop = is_closed_loop(geom)
            if is_loop and length < loop_threshold:
                results.candidate_oids.add(oid)
                results.small_loops += 1
                continue
            
            #3. Detect self-intersections
            weird_geometry = detect_suspect_geometry(geom)
            if weird_geometry:
                smallest_gap = min(end-start for _, start, end in weird_geometry)
                if smallest_gap < MAX_DUPLICATE_GAP:
                    results.suspect_oids.add(oid)
                    continue
                
            #< Old duplicate script went here >

            #4. Exclude any remaining features touching the AOI boundary and remove floating segments
            if oid in results.touching_oids:
                continue
            
            if is_floating_segment(geom, endpoint_counts) and length <= 20:
                results.candidate_oids.add(oid)
                results.floating += 1
                continue
        end_time = time.time() - t0
        arcpy.AddMessage(f"Looped through all features for initial artifact detection {end_time}.")

def remove_artifacts(fc, loop_threshold, aoi):
    """Removes dangles, 3/4 way intersections, and redundant lines."""
    
    counts = {
        "small_loops" : 0,
        "fragments" : 0,
        "floating" : 0,
        "other_artifacts" : 0}
        
    uid = str(uuid.uuid4())[:8]
    
    working_layer = f"working_layer_{uid}"
    dissolved_fc = rf"memory\dissolved_fc_{uid}"
    out_fc = os.path.join(os.path.dirname(fc), f"cleaned_fc_{uid}")
    
    temp_items = [working_layer, dissolved_fc,
                  rf"memory\knot_fc_{uid}",
                  rf"memory\no_artifacts_{uid}",
                  rf"memory\joined_fc_{uid}"]
    try:
        from dataclasses import dataclass, field

        @dataclass
        class ScanResults:
            touching_oids: set = field(default_factory = set)
            suspect_oids: set = field(default_factory = set)
            candidate_oids: set = field(default_factory = set)

            fragment_count: int = 0
            loop_count: int = 0
            floating_count: int = 0

        arcpy.management.CopyFeatures(fc, out_fc)
        out_fc_oid_field = arcpy.Describe(fc).OIDFieldName
        #Add ORIG_ID field to the features
        if "ORIG_ID" not in [f.name for f in arcpy.ListFields(out_fc)]:
            arcpy.management.AddField(
                out_fc,
                "ORIG_ID",
                "LONG"
            )
            with arcpy.da.UpdateCursor(out_fc, ["OID@", "ORIG_ID"]) as cursor:
                for oid, _ in cursor:
                    cursor.updateRow([oid, oid])

        #Determine which features are touching the AOI boundaries so they can be excluded
        touching_oids = touching_boundary(out_fc, working_layer, aoi)
        results = ScanResults(touching_oids= touching_oids)

        arcpy.AddMessage(f"Features touching AOI boundary detected: {len(results.touching_oids)}")

        #-- scan features --
        arcpy.AddMessage("Scanning features...") 
        scan_features(out_fc, results, loop_threshold)

        arcpy.AddMessage("Detecting knots...")
        removal_candidates, dissolved_fc = detect_knots(
            out_fc = out_fc, 
            suspect_artifact_ids = results.suspect_oids, 
            uid = uid, 
            dissolved_fc= dissolved_fc, 
            out_fc_oid_field=out_fc_oid_field)
        
        arcpy.AddMessage("Replacing suspect geometry...")
        out_fc = replace_suspect_geometry(
                    out_fc,
                    results.suspect_oids,
                    dissolved_fc,
                    removal_candidates,
                    uid,
                    counts,
                    out_fc_oid_field
                )

        total_artifacts = (len(results.candidate_oids) + len(removal_candidates))
        if total_artifacts == 0:
            arcpy.AddMessage(f"No artifacts left to remove. Nice!")
            return out_fc, 0
        else:
            arcpy.AddMessage(f"Candidate features identified for removal: {results.candidate_oids}")
            arcpy.AddMessage("\n --- Artifact Summary ---")
            arcpy.AddMessage(f"Fragments: {results.fragment_count}")
            arcpy.AddMessage(f"Small loops: {results.loop_count}")
            arcpy.AddMessage(f"Floating: {results.floating_count}")

        # -- Remove candidates ---
        arcpy.AddMessage(f"Removing candidates...")
        deleter(working_layer)
        make_layer(out_fc, results.candidate_oids, out_fc_oid_field, working_layer, "IN")
        selected = int(arcpy.management.GetCount(working_layer)[0])
        before = int(arcpy.management.GetCount(out_fc)[0])

        if selected:
            arcpy.management.DeleteFeatures(working_layer)
            arcpy.AddMessage(f"Deleted {selected} artifacts")
        after = int(arcpy.management.GetCount(out_fc)[0])
        arcpy.AddMessage(f"Before = {before}")
        arcpy.AddMessage(f"After = {after}")

        return out_fc, selected

    finally:
        for item in temp_items:
            try: deleter(item)
            except Exception as e:
                arcpy.AddMessage(f"Warning: could not delete {item}: {e}")

def clean_contours(in_fc, working_area, loop_threshold, out_fc):
    """Executes the remove artifacts function. Performs a low-threshold simplify line to reduce the vertex count and speed up processing."""

    arcpy.AddMessage("Starting artifact removal -----")
    
    #Environment settings ---------------------
    arcpy.env.extent = None
    
    #File paths  --------------------------------
    arcpy.AddMessage("Refreshing copy of contour file...")
    deleter(out_fc)
    arcpy.cartography.SimplifyLine(
        in_features= in_fc,
        out_feature_class = out_fc,
        algorithm = "POINT_REMOVE",
        tolerance = 1,
        collapsed_point_option = "NO_KEEP"
    )

    #Build aoi ---------------------------
    aoi = build_aoi_boundary(working_area)

    #Diagnostics
    count = int(arcpy.management.GetCount(out_fc)[0])
    arcpy.AddMessage(f"Initial feature count: {count}")
    
    #Run artifact cleaner -------------------------------
    new_fc, deleted = remove_artifacts(
        out_fc, 
        loop_threshold,
        aoi)

    arcpy.management.Delete(out_fc)
    arcpy.management.CopyFeatures(new_fc, out_fc)

    if new_fc != out_fc and arcpy.Exists(new_fc):
        arcpy.management.Delete(new_fc)
        arcpy.AddMessage(f"Deleted temp file: {new_fc}")

    arcpy.AddMessage("Clean up complete.")
    return out_fc