def get_oid_fields(out_fc, ids):
    target_oid_field = arcpy.Describe(out_fc).OIDFieldName
    oid_string = ",".join(map(str, ids))
    return target_oid_field, oid_string

def make_layer(in_fc, oid_list, layer_name, in_not_in):
    oid_fields, oid_list = get_oid_fields(in_fc, oid_list)
    layer = arcpy.management.MakeFeatureLayer(
        in_fc,
        f"{layer_name}",
        f"{oid_fields} {in_not_in} ({oid_list})"
    )
    return layer

out_fc_oid_field = arcpy.Describe(out_fc).OIDFieldName


knot_layer = make_layer(out_fc, suspect_artifact_ids, out_fc_oid_field, "knot_layer", "IN")
tiny_fragments = make_layer(dissolved_fc, small_fragments, "tiny_fragments", "IN")
duplicate_fragments = make_layer(dissolved_fc, duplicate_fragment_oids, "duplicate_fragments", "IN")
target_layer = make_layer(dissolved_fc, artifact_candidates, "target_layer", "NOT IN")
repair_layer = make_layer(out_fc, suspect_artifact_ids, out_fc_oid_field, "repair_layer", "IN")

def make_layer(fc, id_list, oid_fields, layer_name, in_not_in):
    oid_string = ",".join(map(str, id_list))
    layer = arcpy.management.MakeFeatureLayer(
            fc,
            f"{layer_name}",
            f"{oid_fields} {in_not_in} ({oid_string})"
        )
    return layer