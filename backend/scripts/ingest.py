"""
Ingest onsen data into ChromaDB.

Usage (from project root):
    python backend/scripts/ingest.py --file data/okinawa_springs.jsonl

Reads raw Japanese JSONL, translates name/city/sales_point via OpenAI,
applies static lookups for prefecture and spa quality, then upserts into ChromaDB.
"""

import argparse
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

import openai
from core.config import settings
from vectorstore.store import get_collection

# ── Static lookups ──────────────────────────────────────────────────────────

SPA_QUALITY_MAP = {
    "単純温泉": "Simple Spring",
    "炭酸水素塩泉": "Bicarbonate Spring",
    "塩化物泉": "Chloride Spring",
    "硫酸塩泉": "Sulfate Spring",
    "含鉄泉": "Iron Spring",
    "硫黄泉": "Sulfur Spring",
    "酸性泉": "Acidic Spring",
    "放射能泉": "Radon Spring",
    "含よう素泉": "Iodine Spring",
    "二酸化炭素泉": "Carbon Dioxide Spring",
    "含アルミニウム泉": "Aluminium Spring",
    "含銅鉄泉": "Copper-Iron Spring",
    "その他": "Other",
}

PREFECTURE_MAP = {
    "北海道": "Hokkaido", "青森県": "Aomori", "岩手県": "Iwate", "宮城県": "Miyagi",
    "秋田県": "Akita", "山形県": "Yamagata", "福島県": "Fukushima", "茨城県": "Ibaraki",
    "栃木県": "Tochigi", "群馬県": "Gunma", "埼玉県": "Saitama", "千葉県": "Chiba",
    "東京都": "Tokyo", "神奈川県": "Kanagawa", "新潟県": "Niigata", "富山県": "Toyama",
    "石川県": "Ishikawa", "福井県": "Fukui", "山梨県": "Yamanashi", "長野県": "Nagano",
    "岐阜県": "Gifu", "静岡県": "Shizuoka", "愛知県": "Aichi", "三重県": "Mie",
    "滋賀県": "Shiga", "京都府": "Kyoto", "大阪府": "Osaka", "兵庫県": "Hyogo",
    "奈良県": "Nara", "和歌山県": "Wakayama", "鳥取県": "Tottori", "島根県": "Shimane",
    "岡山県": "Okayama", "広島県": "Hiroshima", "山口県": "Yamaguchi", "徳島県": "Tokushima",
    "香川県": "Kagawa", "愛媛県": "Ehime", "高知県": "Kochi", "福岡県": "Fukuoka",
    "佐賀県": "Saga", "長崎県": "Nagasaki", "熊本県": "Kumamoto", "大分県": "Oita",
    "宮崎県": "Miyazaki", "鹿児島県": "Kagoshima", "沖縄県": "Okinawa",
}

# ── Translation ──────────────────────────────────────────────────────────────

client = openai.OpenAI(api_key=settings.openai_api_key)


def translate_batch(items: list[dict]) -> list[dict]:
    """Translate name, city, and sales_point for a batch of records."""
    payload = json.dumps(
        [{"id": i, "name": r["name"], "city": r["city_ja"], "sales_point": r["sales_point"]}
         for i, r in enumerate(items)],
        ensure_ascii=False,
    )
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "You translate Japanese onsen data to English. "
                    "Return a JSON array with the same ids. "
                    "Each object must have: id, name_en (romanised/translated name), "
                    "city_en (English city/district name), sales_point_en (natural English description). "
                    "Output only valid JSON, no markdown."
                ),
            },
            {"role": "user", "content": payload},
        ],
        temperature=0,
    )
    return json.loads(response.choices[0].message.content)


# ── ChromaDB ─────────────────────────────────────────────────────────────────
# The collection (path, name "onsen_springs", and text-embedding-3-small embedding
# function) is owned by vectorstore.store.get_collection — imported above. Reusing
# it guarantees the ingest job writes to the SAME ChromaDB path the app reads from
# (settings.chroma_path), instead of a separately computed path.


# ── Main ─────────────────────────────────────────────────────────────────────

def parse_location(location: str | None) -> tuple[str, str]:
    parts = (location or "").strip().split(" ", 1)
    prefecture_ja = parts[0] if parts else ""
    city_ja = parts[1] if len(parts) > 1 else ""
    return prefecture_ja, city_ja


def translate_spa_quality(quality_ja: str | None) -> str:
    # Some records have a null/empty spa_quality; treat that as "no info".
    if not quality_ja:
        return ""
    parts = [p.strip() for p in quality_ja.split("、")]
    translated = [SPA_QUALITY_MAP.get(p, p) for p in parts]
    return ", ".join(translated)


def build_document(record: dict, translation: dict) -> str:
    """The text embedded for retrieval.

    Prefer the translated sales pitch, then the original Japanese one. Some
    records have an empty sales_point — embedding "" gives a meaningless vector
    (and can error at the embeddings API), so fall back to name + prefecture and,
    in the worst case, a constant, guaranteeing a non-empty document.
    """
    name = translation.get("name_en") or record.get("name", "")
    fallback = ". ".join(p for p in (name, record.get("prefecture_en", "")) if p)
    return (
        translation.get("sales_point_en")
        or record.get("sales_point")
        or fallback
        or "onsen"
    )


def ingest(jsonl_path: Path, batch_size: int = 20) -> None:
    records = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Loaded {len(records)} records from {jsonl_path.name}")

    # Add derived fields before translation
    for r in records:
        prefecture_ja, city_ja = parse_location(r["location"])
        r["prefecture_ja"] = prefecture_ja
        r["city_ja"] = city_ja
        r["prefecture_en"] = PREFECTURE_MAP.get(prefecture_ja, prefecture_ja)
        r["spa_quality_en"] = translate_spa_quality(r["spa_quality"])
        r["region_slug"] = jsonl_path.stem.replace("_springs", "")

    # Translate in batches
    collection = get_collection()
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        print(f"Translating batch {i // batch_size + 1} ({len(batch)} records)...")
        translations = translate_batch(batch)
        trans_map = {t["id"]: t for t in translations}

        ids, documents, metadatas = [], [], []
        for j, record in enumerate(batch):
            t = trans_map.get(j, {})
            doc = build_document(record, t)
            meta = {
                "name": record["name"],
                "name_en": t.get("name_en", ""),
                "prefecture_en": record["prefecture_en"],
                "city_en": t.get("city_en", ""),
                "spa_quality_en": record["spa_quality_en"],
                "region_slug": record["region_slug"],
                "detail_url": record["detail_url"],
            }
            ids.append(record["detail_url"])
            documents.append(doc)
            metadatas.append(meta)

        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
        print(f"  Upserted {len(ids)} records.")

    print(f"\nDone. Total in collection: {collection.count()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest onsen JSONL into ChromaDB")
    parser.add_argument("--file", required=True, help="Path to .jsonl file")
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()

    ingest(Path(args.file), batch_size=args.batch_size)
