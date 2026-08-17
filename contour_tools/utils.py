"""
utils.py
Some small helper functions and constants for use in the Contours Toolbox.
"""

import arcpy

ROUND = 6

#Buffer/overlap in metres around each grid section.
BUFF = 5

#Maximum number of cleanup -> dissolve -> loop iterations that can occur in cleanup.py. Intentionally kept small.
MAX_ITERATIONS = 4

def deleter(feature):
    """Checks if an item exists and deletes it."""
    if arcpy.Exists(feature):
        arcpy.management.Delete(feature)

def purger(purge_list):
    """Checks whether each item in a list of items exists and deletes them if yes."""
    for feature in purge_list:
        deleter(feature)

def rounded_xy(point):
    return(
        round(point.X, ROUND),
        round(point.Y, ROUND))

def get_points(geom):
    return (rounded_xy(geom.firstPoint), rounded_xy(geom.lastPoint))

def is_closed_loop(geom):
    """Detects whether a loop is closed or not based on whether its endpoints share the same coordinates. Used in cleanup.py"""
    if not geom or geom.pointCount < 3:
        return False
    start, end = get_points(geom)
    return start == end
