"""Unit tests for backend.utils.diff (compute_diff + parse_diff_lines)."""

from __future__ import annotations

from backend.utils.diff import compute_diff, parse_diff_lines

OLD = "alpha\nbeta\ngamma\ndelta\n"
NEW = "alpha\nbeta\ngamma modified\ndelta\n"


class TestComputeDiff:
    def test_produces_unified_diff_string(self):
        result = compute_diff(OLD, NEW)
        assert isinstance(result, str)
        assert "---" in result
        assert "+++" in result
        assert "-gamma" in result
        assert "+gamma modified" in result

    def test_identical_content_gives_empty_diff(self):
        result = compute_diff(OLD, OLD)
        assert result == ""

    def test_empty_old_shows_all_additions(self):
        result = compute_diff("", "hello\n")
        assert "+hello" in result

    def test_context_parameter_controls_lines(self):
        # With context=0 only changed lines (and hunk headers) appear — no ctx lines
        long_old = "\n".join(f"line{i}" for i in range(20)) + "\n"
        long_new = long_old.replace("line10", "line10_changed")
        diff0 = compute_diff(long_old, long_new, context=0)
        diff5 = compute_diff(long_old, long_new, context=5)
        # context=5 diff should be longer
        assert len(diff5.splitlines()) > len(diff0.splitlines())

    def test_returns_string_for_empty_inputs(self):
        assert compute_diff("", "") == ""


class TestParseDiffLines:
    def test_parses_addition(self):
        diff = compute_diff("a\n", "a\nb\n")
        lines = parse_diff_lines(diff)
        adds = [ln for ln in lines if ln["kind"] == "add"]
        assert len(adds) == 1
        assert adds[0]["content"] == "b"

    def test_parses_deletion(self):
        diff = compute_diff("a\nb\n", "a\n")
        lines = parse_diff_lines(diff)
        dels = [ln for ln in lines if ln["kind"] == "del"]
        assert len(dels) == 1
        assert dels[0]["content"] == "b"

    def test_hunk_rows_flagged(self):
        diff = compute_diff(OLD, NEW)
        lines = parse_diff_lines(diff)
        hunks = [ln for ln in lines if ln.get("is_hunk")]
        assert len(hunks) >= 1

    def test_addition_has_new_num_not_old_num(self):
        diff = compute_diff("x\n", "x\ny\n")
        lines = parse_diff_lines(diff)
        adds = [ln for ln in lines if ln["kind"] == "add"]
        for ln in adds:
            assert ln["new_num"] is not None
            assert ln["old_num"] is None

    def test_deletion_has_old_num_not_new_num(self):
        diff = compute_diff("x\ny\n", "x\n")
        lines = parse_diff_lines(diff)
        dels = [ln for ln in lines if ln["kind"] == "del"]
        for ln in dels:
            assert ln["old_num"] is not None
            assert ln["new_num"] is None

    def test_context_line_has_both_nums(self):
        diff = compute_diff(OLD, NEW, context=3)
        lines = parse_diff_lines(diff)
        ctx = [ln for ln in lines if ln["kind"] == "ctx"]
        assert len(ctx) > 0
        for ln in ctx:
            assert ln["old_num"] is not None
            assert ln["new_num"] is not None

    def test_empty_diff_returns_empty_list(self):
        assert parse_diff_lines("") == []
        assert parse_diff_lines(None) == []

    def test_line_numbers_are_monotonically_increasing(self):
        diff = compute_diff("a\nb\nc\n", "a\nb modified\nc\n", context=3)
        lines = parse_diff_lines(diff)
        old_nums = [ln["old_num"] for ln in lines if ln["old_num"] is not None]
        new_nums = [ln["new_num"] for ln in lines if ln["new_num"] is not None]
        assert old_nums == sorted(old_nums)
        assert new_nums == sorted(new_nums)
