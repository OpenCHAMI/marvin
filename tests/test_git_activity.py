from openchami_coding_agent.git_activity import parse_numstat_output, parse_status_porcelain


def test_parse_numstat_output_sums_added_deleted() -> None:
    output = "12\t3\tsrc/a.py\n5\t0\tsrc/b.py\n-\t-\tbinary.dat\n"
    assert parse_numstat_output(output) == (17, 3)


def test_parse_status_porcelain_extracts_files() -> None:
    output = " M src/a.py\nA  src/b.py\nR  old.py -> new.py\n?? notes.txt\n"
    count, files = parse_status_porcelain(output)
    assert count == 4
    assert files == ["src/a.py", "src/b.py", "new.py", "notes.txt"]