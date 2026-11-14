"""Build mark: a placeholder that tools/make_tester_package.py overwrites in
each tester's copy with a random code, so a leaked copy can be traced to the
copy it came from. It says nothing about the tester or their PC.
Keep the length: 7 + 24 characters."""

MARK = "LCMARK:000000000000000000000000"


def code() -> str:
    return MARK[7:]
