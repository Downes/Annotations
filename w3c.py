import json
import os

BASE_URL = os.environ.get("BASE_URL", "https://annotations.mooc.ca")


def to_w3c_annotation(row: dict) -> dict:
    """Convert a database row dict to W3C Web Annotation JSON-LD."""
    tags = json.loads(row["tags"]) if isinstance(row["tags"], str) else (row["tags"] or [])
    selector = json.loads(row["target_selector"]) if row.get("target_selector") else None

    anno = {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "id": f"{BASE_URL}/annotations/{row['id']}",
        "type": "Annotation",
        "motivation": row["motivation"],
        "creator": {
            "id": row["creator"],
            "type": "Person",
        },
        "created": row["created"],
        "modified": row.get("modified"),
        "body": {
            "type": "TextualBody",
            "value": row["body"],
            "format": "text/markdown",
        },
        "target": {
            "source": row["target_url"],
            "selector": selector,
        },
    }

    if tags:
        anno["tag"] = tags

    return anno


def to_w3c_page(rows: list[dict], total: int, offset: int) -> dict:
    """Wrap a list of annotations in a W3C AnnotationPage."""
    return {
        "@context": "http://www.w3.org/ns/anno.jsonld",
        "type": "AnnotationPage",
        "startIndex": offset,
        "total": total,
        "items": [to_w3c_annotation(r) for r in rows],
    }
