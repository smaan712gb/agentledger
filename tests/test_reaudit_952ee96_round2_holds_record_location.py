"""A firm created before the platform recorded where its objects live: offboarding refuses at the object-store stage
(never guessing the place from its own settings) until an operator records the location, which is then the only one
the offboarding removes objects from; a recorded location is never changed."""

from __future__ import annotations

import pytest

from agentledger.security.platform import AuthError
from test_reaudit_952ee96_round2_holds_blob_location_from_environment import (  # noqa: F401  (bucket is a fixture)
    FIRM, OFFBOARD, RECORDED, _firm_with_w2, _key_alive, bucket)


def test_an_unrecorded_location_is_refused_until_an_operator_records_it(tmp_path, monkeypatch, bucket):  # noqa: F811
    plat, obj = _firm_with_w2(tmp_path)
    plat.conn.execute("UPDATE firms SET blob_location = NULL WHERE id = ?", (FIRM,))     # as upgraded from before
    with pytest.raises(AuthError, match="never recorded"):
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)                 # even with the right settings: not guessed
    assert "blobs_removed" not in plat._stages(FIRM) and list(bucket.objects) == [obj] and _key_alive(plat)
    assert plat.firm(FIRM)["status"] == "offboarding"

    with pytest.raises(AuthError, match="say why"):
        plat.record_blob_location(FIRM, RECORDED, by="ops", reason=" ")
    with pytest.raises(AuthError, match="'file' or 's3:"):
        plat.record_blob_location(FIRM, "r2://evidence", by="ops", reason="checked the API's settings")
    plat.record_blob_location(FIRM, RECORDED, by="ops", reason="checked against the production API's settings")
    with pytest.raises(AuthError, match="never changes"):
        plat.record_blob_location(FIRM, "file", by="ops", reason="second thoughts")
    assert "firm_blob_location_recorded" in [e["event"] for e in plat.events(FIRM)]

    with monkeypatch.context() as m:                                  # a job without the object-store settings
        m.delenv("AGENTLEDGER_BLOBS")
        with pytest.raises(AuthError, match="configure that object store"):
            plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert list(bucket.objects) == [obj]
    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)                 # the recorded store: the offboarding finishes
    assert not bucket.objects and plat.firm(FIRM)["status"] == "deleted" and not _key_alive(plat)
