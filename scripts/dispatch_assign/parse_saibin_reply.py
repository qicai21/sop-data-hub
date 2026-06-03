"""Parse a 赛彬-style 分票回复 into structured assignments.

Supported line forms (whitespace tolerant; Chinese / arrow variations OK):

  车号 1234567 -> lot01
  车号 1234567 箱号 7654321 -> lot01
  车号 1234567 箱号 7654321,8765432 -> lot01
  车号 1234567 箱号 7654321 -> lot01, 箱号 8765432 -> lot02
  其余 -> lot03
  其余全部 -> lot03

Result for each row: zero or more (car_no, container_no_or_None, lot) assignments,
plus an optional 'rest' lot for unassigned cars.

Numeric tolerance: car_no = 7 digits, container = 7 digits. Numbers with other
lengths get warning flags but are not silently dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Assignment:
    car_no: str
    container_no: str | None
    lot: str
    source_line: str


@dataclass
class ParseResult:
    assignments: list[Assignment] = field(default_factory=list)
    rest_lot: str | None = None
    warnings: list[str] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)


LOT_RE = re.compile(r"lot\s*0*([0-9]+)", re.IGNORECASE)
ARROW_RE = re.compile(r"->|→|=>|—>|至|到")


def _norm_lot(text: str) -> str | None:
    m = LOT_RE.search(text)
    if not m:
        return None
    return f"lot{int(m.group(1)):02d}"


def _extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d{4,}", text)


def parse_reply(text: str) -> ParseResult:
    result = ParseResult()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        result.raw_lines.append(line)

        # "其余 / 其余全部 -> lotXX" (允许 "4. 其余全部 -> lot3" 这种前缀)
        line_stripped = re.sub(r"^\s*\d+[.、)]\s*", "", line)
        if line_stripped.startswith(("其余", "余下", "余的", "剩余")):
            lot = _norm_lot(line)
            if not lot:
                result.warnings.append(f"rest line missing lot: {line!r}")
                continue
            if result.rest_lot and result.rest_lot != lot:
                result.warnings.append(
                    f"multiple rest lots: {result.rest_lot} vs {lot}")
            result.rest_lot = lot
            continue

        # split into sub-clauses by "," / ",  / "；" — each sub-clause has its own lot
        # but a line may share a single car_no across multiple sub-clauses
        car_no = None
        nums = _extract_numbers(line)
        if not nums:
            result.warnings.append(f"no numeric tokens: {line!r}")
            continue
        # heuristic: first 7-digit number that comes after "车号" or is the first
        # number on the line, is the car_no
        car_match = re.search(r"车\s*号?\s*[:：]?\s*(\d{6,8})", line)
        if car_match:
            car_no = car_match.group(1)
        else:
            # fall back to first number
            car_no = nums[0]

        # Now find container/lot pairs.
        # Pattern 1: "箱号 XXXXXXX -> lot, 箱号 YYYYYYY -> lot"
        # Pattern 2: "箱号 XXXXXXX,YYYYYYY -> lot"
        # Pattern 3: "车号 XXXXXXX -> lot" (no container)
        sub_clauses = re.split(r"[,，;；]", line)
        any_assigned = False
        for sc in sub_clauses:
            sc = sc.strip()
            if not sc:
                continue
            lot = _norm_lot(sc)
            if not lot:
                # this clause has no lot; skip (will be picked up by next clause)
                continue
            # find containers in this clause
            box_match = re.search(r"箱\s*号?\s*[:：]?\s*([\d,，]+)", sc)
            if box_match:
                box_tokens = re.findall(r"\d{4,}", box_match.group(1))
                # exclude the lot number itself
                lot_num_re = re.compile(rf"^0*{int(lot[3:])}$")
                box_tokens = [b for b in box_tokens if not lot_num_re.match(b)]
                for box in box_tokens:
                    if len(box) not in (7,):
                        result.warnings.append(
                            f"container_no length != 7 ({len(box)}): {box} on line {line!r}")
                    result.assignments.append(
                        Assignment(car_no=car_no, container_no=box, lot=lot, source_line=line))
                    any_assigned = True
            else:
                # no container — whole car
                if len(car_no) not in (7,):
                    result.warnings.append(
                        f"car_no length != 7 ({len(car_no)}): {car_no} on line {line!r}")
                result.assignments.append(
                    Assignment(car_no=car_no, container_no=None, lot=lot, source_line=line))
                any_assigned = True

        if not any_assigned:
            result.warnings.append(f"no lot found on line: {line!r}")

    return result


# ── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse, sys
    p = argparse.ArgumentParser()
    p.add_argument("--text", help="text to parse")
    p.add_argument("--file", help="read text from file")
    args = p.parse_args()
    text = args.text or (open(args.file).read() if args.file else sys.stdin.read())
    r = parse_reply(text)
    print(f"=== parsed {len(r.assignments)} assignments, rest={r.rest_lot} ===")
    for a in r.assignments:
        print(f"  car={a.car_no} box={a.container_no} -> {a.lot}")
    if r.rest_lot:
        print(f"  其余 -> {r.rest_lot}")
    if r.warnings:
        print(f"\n=== warnings ({len(r.warnings)}) ===")
        for w in r.warnings:
            print(f"  {w}")


if __name__ == "__main__":
    main()
