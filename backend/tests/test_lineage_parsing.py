"""
Lineage row parsing — the non-trivial algorithmic logic that would silently
break during refactors. Pure Python; no DB dependencies.
"""
from backend.lineage_service import _parse_lineage_ref


class TestParseLineageRef:
    def test_plain_table_reference(self):
        node_id, node_type = _parse_lineage_ref(
            "cat.schema.mytable", None, "TABLE"
        )
        assert node_id == "cat.schema.mytable"
        assert node_type == "TABLE"

    def test_view_type_preserved(self):
        node_id, node_type = _parse_lineage_ref(
            "cat.schema.myview", None, "VIEW"
        )
        assert node_type == "VIEW"

    def test_materialized_view_type_preserved(self):
        _, node_type = _parse_lineage_ref("c.s.mv", None, "MATERIALIZED_VIEW")
        assert node_type == "MATERIALIZED_VIEW"

    def test_streaming_table_type_preserved(self):
        _, node_type = _parse_lineage_ref("c.s.st", None, "STREAMING_TABLE")
        assert node_type == "STREAMING_TABLE"

    def test_unknown_type_falls_back_to_table(self):
        _, node_type = _parse_lineage_ref("c.s.t", None, None)
        assert node_type == "TABLE"

    def test_volume_path_parsed_as_volume(self):
        node_id, node_type = _parse_lineage_ref(
            None, "/Volumes/mycatalog/myschema/myvol/data.csv", None
        )
        assert node_id == "mycatalog.myschema.myvol"
        assert node_type == "VOLUME"

    def test_short_volume_path_fallback(self):
        node_id, node_type = _parse_lineage_ref(
            None, "/Volumes/onlyone", None
        )
        assert node_type == "VOLUME"
        assert node_id.startswith("volume:")

    def test_s3_path_parsed_as_path(self):
        node_id, node_type = _parse_lineage_ref(
            None, "s3://my-bucket/prefix/file.parquet", None
        )
        assert node_id == "path:s3://my-bucket"
        assert node_type == "PATH"

    def test_abfss_path_parsed_as_path(self):
        node_id, node_type = _parse_lineage_ref(
            None, "abfss://container@account.dfs.core.windows.net/data/", None
        )
        assert node_type == "PATH"
        assert "abfss" in node_id

    def test_generic_path_truncated(self):
        long_path = "/some/very/long/path/" + ("a" * 200)
        node_id, node_type = _parse_lineage_ref(None, long_path, None)
        assert node_type == "PATH"
        # ID is truncated to 80 chars of the path (plus "path:" prefix)
        assert len(node_id) <= len("path:") + 80

    def test_both_none_returns_none(self):
        assert _parse_lineage_ref(None, None, None) == (None, None)

    def test_table_name_takes_priority_over_path(self):
        # When both are present, the table name wins
        node_id, _ = _parse_lineage_ref("c.s.t", "s3://bucket/thing", "TABLE")
        assert node_id == "c.s.t"
