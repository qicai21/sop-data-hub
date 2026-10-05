from __future__ import annotations

from sop_hub.sop.departure_text_parser import parse_jilin_departure_segments
from sop_hub.sop.jilin_mixed_departure import partition_ticket_cluster
from sop_hub.sop.jilin_mixed_departure import partition_ticket_cluster_by_manual_cars
from sop_hub.sop.jilin_mixed_departure import select_exact_ticket_cluster
from sop_hub.sop.jilin_mixed_departure import _has_complete_unique_portal_evidence
from sop_hub.sop.executor_runner import _restrict_jilin_to_exact_ticket_cluster
from sop_hub.sop.departure_text_parser import DepartureCandidate
from sop_hub.sop.shipment_query_window import QueryWindow
from sop_hub.sop.shipment_query_window import ShipmentQueryResult
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
    partitions = partition_ticket_cluster(
        tickets, segments, allow_inferred_ticket_order=True,
    )
    assert len(partitions) == 2
    assert len(partitions[0][1]) == 22
    assert partitions[0][1][0].ydid.endswith("873")
    assert partitions[0][1][-1].ydid.endswith("894")
    assert len(partitions[1][1]) == 23
    assert partitions[1][1][0].ydid.endswith("895")
    assert partitions[1][1][-1].ydid.endswith("917")


def test_partition_does_not_infer_physical_car_order_from_ydid_order():
    segments = parse_jilin_departure_segments(TEXT)
    tickets = [
        ShipmentCandidate(ydid=str(number), wagon_no=str(1500000 + number))
        for number in range(873, 918)
    ]

    assert partition_ticket_cluster(tickets, segments) == []


def test_manual_car_assignment_partitions_only_an_exact_complete_cluster():
    tickets = [
        ShipmentCandidate(ydid="ydid-1", wagon_no="1817398"),
        ShipmentCandidate(ydid="ydid-2", wagon_no="5317394"),
        ShipmentCandidate(ydid="ydid-3", wagon_no="1735852"),
        ShipmentCandidate(ydid="ydid-4", wagon_no="1842181"),
    ]

    partitions, error = partition_ticket_cluster_by_manual_cars(
        tickets,
        {
            "高栏309": ["1817398", "5317394", "1735852"],
            "智港526": ["1842181"],
        },
    )

    assert error == ""
    assert [(segment.ship_name, segment.car_count) for segment, _ in partitions] == [
        ("高栏309", 3),
        ("智港526", 1),
    ]
    assert [ticket.ydid for ticket in partitions[0][1]] == ["ydid-1", "ydid-2", "ydid-3"]


def test_manual_car_assignment_rejects_incomplete_or_outside_cluster_cars():
    tickets = [
        ShipmentCandidate(ydid="ydid-1", wagon_no="1817398"),
        ShipmentCandidate(ydid="ydid-2", wagon_no="5317394"),
    ]

    partitions, error = partition_ticket_cluster_by_manual_cars(
        tickets,
        {"高栏309": ["1817398"], "智港526": ["0000000"]},
    )

    assert partitions == []
    assert "unassigned current cars: 5317394" in error
    assert "cars outside current ticket cluster: 0000000" in error


def test_manual_car_assignment_rejects_repeated_car_numbers():
    tickets = [
        ShipmentCandidate(ydid="ydid-1", wagon_no="1817398"),
        ShipmentCandidate(ydid="ydid-2", wagon_no="5317394"),
    ]

    partitions, error = partition_ticket_cluster_by_manual_cars(
        tickets,
        {"高栏309": ["1817398", "1817398"], "智港526": ["5317394"]},
    )

    assert partitions == []
    assert error == "manual assignments repeat a car number"


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


def test_single_ship_jilin_event_restricts_broad_window_to_its_train():
    first_train = [
        ShipmentCandidate(
            ydid=f"A-{index}", wagon_no=f"A-{index}",
            ticketed_at=f"2026-10-05 14:25:{index:02d}",
        )
        for index in range(37)
    ]
    later_train = [
        ShipmentCandidate(
            ydid=f"B-{index}", wagon_no=f"B-{index}",
            ticketed_at=f"2026-10-05 14:51:{index:02d}",
        )
        for index in range(41)
    ]
    result = ShipmentQueryResult(
        window=QueryWindow.from_reference("2026-10-05 14:25:54", window_before_minutes=720, window_after_minutes=720),
        origin_station="高桥镇",
        destination_station="四平",
        expected_car_count=37,
        total_candidates=78,
        exact_match_count=37,
        ambiguous_count=41,
        candidates=first_train + later_train,
    )
    candidate = DepartureCandidate(
        message_id="wx_37", group_id="铁晟业务工作群",
        message_time="2026-10-05 14:25:54", raw_text="煤二 四平铁 林加尼37节",
        destination="四平", car_count=37, optional_ship_name="林加尼",
        project_id="jilin_jingang_jinzhou", status="complete",
    )

    assert _restrict_jilin_to_exact_ticket_cluster(result, candidate)
    assert [row.ydid for row in result.candidates] == [row.ydid for row in first_train]
    assert result.total_candidates == result.exact_match_count == 37
    assert result.ambiguous_count == 0
