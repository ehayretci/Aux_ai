"""
Knowledge-base Ingestion CLI
============================

Adds a labelled example to the local ChromaDB store. The note text is
embedded by ChromaDB's default embedder; the image path, polarity, category
and timestamp travel as metadata so retrievers can filter by category.

Usage:
    python training/ingest.py \
        --image path/to/screenshot.png \
        --category "Visual Design" \
        --polarity positive \
        --note "Strong visual hierarchy with a clearly distinct primary CTA."

Categories must match the values produced by the specialists exactly:
  - Functional Usability
  - Information Architecture
  - Visual Design
  - Onboarding & Time-to-Value
  - Accessibility
"""

import os
import sys
import uuid
import argparse
from datetime import datetime, timezone


VALID_CATEGORIES = {
    "Functional Usability",
    "Information Architecture",
    "Visual Design",
    "Onboarding & Time-to-Value",
    "Onboarding",            # accept short form too
    "Accessibility",
}

# Map any short forms to canonical category names that match specialist output.
CATEGORY_ALIASES = {
    "Onboarding": "Onboarding & Time-to-Value",
}

VALID_POLARITIES = {"positive", "negative"}


def _canonical_category(name: str) -> str:
    return CATEGORY_ALIASES.get(name, name)


def main():
    parser = argparse.ArgumentParser(
        description="Add a labelled example to the UX knowledge base."
    )
    parser.add_argument("--image", required=True, help="Path to the screenshot.")
    parser.add_argument(
        "--category", required=True,
        choices=sorted(VALID_CATEGORIES),
        help="Specialist category this example illustrates.",
    )
    parser.add_argument(
        "--polarity", required=True,
        choices=sorted(VALID_POLARITIES),
        help="Whether this example is a positive (works well) or negative (problem) case.",
    )
    parser.add_argument(
        "--note", required=True,
        help="The teaching note. The model will see this verbatim during retrieval.",
    )
    args = parser.parse_args()

    if not os.path.exists(args.image):
        print(f"Error: image not found: {args.image}", file=sys.stderr)
        sys.exit(1)

    # Lazy imports so help/errors don't pay the chromadb import cost.
    from training import retriever as retriever_mod

    col = retriever_mod._ensure_client()
    if col is None:
        print("Error: knowledge base unavailable (chromadb failed to load).", file=sys.stderr)
        sys.exit(2)

    category = _canonical_category(args.category)
    abs_image = os.path.abspath(args.image)
    example_id = f"ex_{uuid.uuid4().hex[:12]}"
    metadata = {
        "image_path": abs_image,
        "category": category,
        "polarity": args.polarity,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    col.add(
        ids=[example_id],
        documents=[args.note.strip()],
        metadatas=[metadata],
    )

    print(f"Stored example {example_id}")
    print(f"  category : {category}")
    print(f"  polarity : {args.polarity}")
    print(f"  image    : {abs_image}")
    print(f"  note     : {args.note.strip()[:160]}")
    print(f"Knowledge base now contains {col.count()} example(s).")


if __name__ == "__main__":
    main()
