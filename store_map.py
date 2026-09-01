from __future__ import annotations

from html import escape
from typing import Any, Iterable


DEFAULT_STORE_ZONES = (
    "หน้าร้าน/ทางเข้า",
    "จุดโปรโมชั่น",
    "ทางเดิน 1",
    "ทางเดิน 2",
    "ทางเดิน 3",
    "ทางเดิน 4",
    "ตู้แช่เย็น",
    "ตู้แช่แข็ง",
    "หน้าแคชเชียร์",
    "คลังหลังร้าน",
)

LOCATION_TEXT_FIELDS = ("branch", "floor", "zone", "aisle", "rack", "shelf")


def parse_store_zones(raw: str | None) -> list[str]:
    values = [part.strip()[:80] for part in str(raw or "").split(",") if part.strip()]
    unique = list(dict.fromkeys(values))
    return unique or list(DEFAULT_STORE_ZONES)


def _coordinate(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if 1 <= number <= 10 else 0


def normalize_location(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    location: dict[str, Any] = {
        field: str(source.get(field) or "").strip()[:100]
        for field in LOCATION_TEXT_FIELDS
    }
    location["map_x"] = _coordinate(source.get("map_x"))
    location["map_y"] = _coordinate(source.get("map_y"))
    return location


def is_complete_location(value: Any) -> bool:
    location = normalize_location(value)
    return bool(
        location["branch"]
        and location["floor"]
        and location["zone"]
        and location["map_x"]
        and location["map_y"]
    )


def location_identity(value: Any) -> tuple[Any, ...]:
    location = normalize_location(value)
    return tuple(location[field].casefold() for field in LOCATION_TEXT_FIELDS) + (
        location["map_x"],
        location["map_y"],
    )


def locations_differ(previous: Any, current: Any) -> bool:
    if not is_complete_location(previous):
        return False
    return location_identity(previous) != location_identity(current)


def location_label(value: Any) -> str:
    location = normalize_location(value)
    parts = [location["branch"], location["floor"], location["zone"]]
    for key, prefix in (("aisle", "ทางเดิน"), ("rack", "ชั้นวาง"), ("shelf", "ช่อง")):
        if location[key]:
            parts.append(f"{prefix} {location[key]}")
    if location["map_x"] and location["map_y"]:
        parts.append(f"พิกัด {location['map_x']},{location['map_y']}")
    return " • ".join(part for part in parts if part) or "ยังไม่ระบุตำแหน่ง"


def current_product_locations(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        (record for record in records if isinstance(record, dict)),
        key=lambda record: str(record.get("submitted_at") or ""),
        reverse=True,
    )
    latest: dict[str, dict[str, Any]] = {}
    for record in ordered:
        barcode = str(record.get("barcode") or "").strip()
        if barcode and barcode not in latest and is_complete_location(record.get("location")):
            latest[barcode] = record
    return list(latest.values())


def store_map_svg(value: Any, *, title: str = "ตำแหน่งสินค้าในร้าน") -> str:
    location = normalize_location(value)
    x_index = location["map_x"] or 1
    y_index = location["map_y"] or 1
    left, top, plot_width, plot_height = 52, 58, 596, 298
    marker_x = left + ((x_index - 0.5) / 10) * plot_width
    marker_y = top + ((y_index - 0.5) / 10) * plot_height
    vertical = "".join(
        f'<line x1="{left + index * plot_width / 10:.1f}" y1="{top}" '
        f'x2="{left + index * plot_width / 10:.1f}" y2="{top + plot_height}" />'
        for index in range(1, 10)
    )
    horizontal = "".join(
        f'<line x1="{left}" y1="{top + index * plot_height / 10:.1f}" '
        f'x2="{left + plot_width}" y2="{top + index * plot_height / 10:.1f}" />'
        for index in range(1, 10)
    )
    safe_title = escape(title)
    safe_zone = escape(location["zone"] or "ยังไม่ระบุโซน")
    safe_branch = escape(location["branch"] or "ยังไม่ระบุสาขา")
    return f"""
    <div style="background:#fff;border:1px solid #e7edf5;border-radius:20px;padding:14px;overflow:auto">
      <svg viewBox="0 0 700 430" role="img" aria-label="{safe_title}" style="width:100%;min-width:520px;height:auto">
        <rect x="16" y="12" width="668" height="402" rx="20" fill="#f7f9fc" stroke="#dce5f0"/>
        <text x="38" y="39" font-family="sans-serif" font-size="18" font-weight="700" fill="#041328">{safe_title}</text>
        <rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" rx="12" fill="#ffffff" stroke="#0969ff" stroke-width="2"/>
        <g stroke="#dfe7f2" stroke-width="1">{vertical}{horizontal}</g>
        <rect x="278" y="365" width="144" height="29" rx="10" fill="#041328"/>
        <text x="350" y="385" text-anchor="middle" font-family="sans-serif" font-size="13" fill="#ffffff">ทางเข้าร้าน</text>
        <circle cx="{marker_x:.1f}" cy="{marker_y:.1f}" r="18" fill="#ff6900" stroke="#ffffff" stroke-width="6"/>
        <circle cx="{marker_x:.1f}" cy="{marker_y:.1f}" r="25" fill="none" stroke="#ff6900" stroke-width="2" opacity=".35"/>
        <text x="{marker_x:.1f}" y="{marker_y + 4:.1f}" text-anchor="middle" font-family="sans-serif" font-size="11" font-weight="800" fill="#ffffff">{x_index},{y_index}</text>
        <text x="38" y="409" font-family="sans-serif" font-size="13" fill="#475467">{safe_branch} • {safe_zone}</text>
      </svg>
    </div>
    """
