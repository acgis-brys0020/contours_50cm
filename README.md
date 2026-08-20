# contours_50cm
Tools for creating and cleaning topographic contours for OMAFA (summer 2026)

**Author**: Sian Bryson

**Organization**: OMAFA Environmental Management Branch, Soils GIS & Technologies unit

**Date**: 2026-08-17

**ArcGIS Version**: 3.5.4

## About
Creates topographic contours from  50 cm LiDAR-derived DTM data and cleans geometry.

## How to use
1. Add toolbox to ArcGIS Pro
2. Either use run full workflow or do Correct imagery -> use the Spatial Analyst Contour tool -> Clip contours if needed -> Clean contours

## Future goals:
- Convert to PyQGIS
- Add automated study area creation
- Compile timing stats automatically
