from types import SimpleNamespace

from gemini_webapi_manager.usage import UsageSnapshot, snapshot_from_client


def _client(info=None, quotas=None):
    return SimpleNamespace(usage_info=info or {}, quotas=quotas or {})


def test_windows_and_binding_percent():
    snap = snapshot_from_client(
        _client(
            {
                "tier": {"id": 2, "label": "PRO"},
                "current_5h": {"usage_percentage": 20, "reset_at": "2030-01-01T00:00:00+00:00",
                               "remaining_credits": 8},
                "weekly": {"usage_percentage": 65, "reset_at": None, "remaining_credits": 3},
            }
        )
    )
    assert snap.tier == "PRO"
    assert snap.five_hour.percent == 20 and snap.weekly.percent == 65
    assert snap.percent == 65


def test_falls_back_to_model_quotas():
    snap = snapshot_from_client(
        _client(quotas={"a": {"usage_percentage": 12.5, "reset_time": 1893456000, "label": "Pro"},
                        "usage_info": {}, "extra": {}})
    )
    assert snap.percent == 12.5
    assert len(snap.model_quotas) == 1 and snap.model_quotas[0]["reset_at"].startswith("2030")


def test_empty_gives_none():
    assert snapshot_from_client(_client()) is None


def test_dict_roundtrip():
    snap = snapshot_from_client(_client({"current_5h": {"usage_percentage": 5}}))
    again = UsageSnapshot.from_dict(snap.to_dict())
    assert again == snap
