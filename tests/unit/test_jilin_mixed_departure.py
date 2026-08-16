from __future__ import annotations

from sop_hub.sop.departure_text_parser import parse_jilin_departure_segments
from sop_hub.sop.jilin_mixed_departure import partition_ticket_cluster
from sop_hub.sop.jilin_mixed_departure import select_exact_ticket_cluster
from sop_hub.sop.jilin_mixed_departure import _has_complete_unique_portal_evidence
from sop_hub.sop.shipment_query_window import ShipmentCandidate


TEXT = (
    "煤三 四平铁 “马兰希望”22节（序号1-22），"
    "”富翔7“23节（序号23-46）"
)


def test_parse_jilin_mixed_departure_segments():
    segments = parse_jilin_departure_segments(TEXT)
    assert [segment.to_dict() for segment in segments] == [
        {
            "ship_name": "马兰希望",
            "car_count": 22,
            "seq_start": 1,
            "seq_end": 22,
        },
        {
            "ship_name": "富翔7",
            "car_count": 23,
            "seq_start": 23,
            "seq_end": 46,
        },
    ]


def test_parse_jilin_mixed_departure_with_total_and_prefix_counts():
    segments = parse_jilin_departure_segments(
        "煤一 32节 四平铁 25节 海洋征服者 ，7节 富翔7"
    )
    assert [segment.to_dict() for segment in segments] == [
        {
            "ship_name": "海洋征服者",
            "car_count": 25,
            "seq_start": None,
            "seq_end": None,
        },
        {
            "ship_name": "富翔7",
            "car_count": 7,
            "seq_start": None,
            "seq_end": None,
        },
    ]


def test_count_only_mixed_departure_cannot_invent_ticket_order():
    segments = parse_jilin_departure_segments(
        "煤一 32节 四平铁 25节 海洋征服者 ，7节 富翔7"
    )
    tickets = [
        ShipmentCandidate(ydid=str(number), wagon_no=str(1500000 + number))
        for number in range(32)
    ]
    assert partition_ticket_cluster(tickets, segments) == []


def test_partition_mixed_ticket_cluster_22_plus_23():
    segments = parse_jilin_departure_segments(TEXT)
    tickets = [
        ShipmentCandidate(
            ydid=f"516322607230455{number:03d}",
            wagon_no=f"{1500000 + number}",
        )
        for number in range(873, 918)
    ]
    partitions = partition_ticket_cluster(tickets, segments)
    assert len(partitions) == 2
    assert len(partitions[0][1]) == 22
    assert partitions[0][1][0].ydid.endswith("873")
    assert partitions[0][1][-1].ydid.endswith("894")
    assert len(partitions[1][1]) == 23
    assert partitions[1][1][0].ydid.endswith("895")
    assert partitions[1][1][-1].ydid.endswith("917")


def test_partition_rejects_total_mismatch():
    segments = parse_jilin_departure_segments(TEXT)
    tickets = [ShipmentCandidate(ydid=str(i), wagon_no=str(i)) for i in range(44)]
    assert partition_ticket_cluster(tickets, segments) == []


def test_complete_unique_portal_ids_are_sufficient_upload_evidence():
    assert _has_complete_unique_portal_evidence(
        total_rows=54, portal_id_count=54, distinct_portal_id_count=54,
    )
    assert not _has_complete_unique_portal_evidence(
        total_rows=54, portal_id_count=54, distinct_portal_id_count=53,
    )


def test_select_exact_ticket_cluster_ignores_other_train():
    old = [
        ShipmentCandidate(
            ydid=f"old-{i}",
            wagon_no=f"old-{i}",
            ticketed_at=f"2026-07-23 03:30:{i:02d}",
        )
        for i in range(10)
    ]
    current = [
        ShipmentCandidate(
            ydid=f"516322607230455{873 + i:03d}",
            wagon_no=str(1500000 + i),
            ticketed_at=f"2026-07-24 07:13:{17 + (i // 5):02d}",
        )
        for i in range(45)
    ]
    selected = select_exact_ticket_cluster(
        old + current,
        expected_count=45,
        reference_time="2026-07-24 07:09:16",
    )
    assert [ticket.ydid for ticket in selected] == [
        ticket.ydid for ticket in current
    ]
