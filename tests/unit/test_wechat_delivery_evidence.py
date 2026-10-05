from sop_hub.sop.wechat_delivery_evidence import select_sent_file_evidence


def test_select_sent_file_evidence_requires_exact_attachment_after_action_time():
    rows = [
        {
            "time": "2026-10-05 21:08:44",
            "file_name": "event.xlsx",
            "appmsg_kind": "file",
        },
        {
            "time": "2026-10-05 21:09:48",
            "file_name": "event.xlsx",
            "appmsg_kind": "file",
            "local_id": 506,
            "file_size": 8504,
        },
        {
            "time": "2026-10-05 21:10:00",
            "file_name": "event.xlsx",
            "appmsg_kind": "text",
        },
        {
            "time": "2026-10-05 21:10:01",
            "file_name": "other.xlsx",
            "appmsg_kind": "file",
        },
    ]

    evidence = select_sent_file_evidence(
        rows, filename="event.xlsx", not_before="2026-10-05T21:08:45+08:00",
    )

    assert evidence == {
        "time": "2026-10-05 21:09:48",
        "local_id": 506,
        "file_name": "event.xlsx",
        "file_size": 8504,
    }
