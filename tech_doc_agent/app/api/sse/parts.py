def stream_part_type_and_data(part) -> tuple[str | None, object]:
    if isinstance(part, dict):
        return part.get("type"), part.get("data")

    if isinstance(part, (tuple, list)) and len(part) == 2:
        return part[0], part[1]

    return None, None


def extract_update_data(part) -> dict:
    if isinstance(part, dict):
        update_data = part.get("data", part)
    elif isinstance(part, (tuple, list)) and len(part) == 2:
        update_data = part[1]
    else:
        update_data = {}

    return update_data if isinstance(update_data, dict) else {}


def extract_message_part_data(part_data) -> tuple[object, dict] | None:
    if not isinstance(part_data, (tuple, list)) or len(part_data) != 2:
        return None

    msg_chunk, metadata = part_data
    return msg_chunk, metadata if isinstance(metadata, dict) else {}


__all__ = [
    "extract_message_part_data",
    "extract_update_data",
    "stream_part_type_and_data",
]
