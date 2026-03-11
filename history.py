# history.py

import time
from collections import defaultdict
from typing import Dict, List, Optional

history_store: Dict[int, List[Dict]] = defaultdict(list)


def add_history_item(user_id: int, prompt: str, response: str, item_type: str = "analysis") -> int:
    item_id = int(time.time())
    item = {
        "id": item_id,
        "timestamp": item_id,
        "prompt": prompt,
        "response": response,
        "type": item_type,
    }
    history_store[user_id].append(item)
    if len(history_store[user_id]) > 20:
        history_store[user_id] = history_store[user_id][-20:]
    return item_id


def get_recent_history(user_id: int, limit: int = 10) -> List[Dict]:
    items = history_store.get(user_id, [])
    return items[-limit:][::-1]


def get_history_item(user_id: int, item_id: int) -> Optional[Dict]:
    for item in history_store.get(user_id, []):
        if item["id"] == item_id:
            return item
    return None


def is_history_fresh(item: Dict, max_age_seconds: Optional[int] = None) -> bool:
    from config import FRESHNESS_SECONDS

    if max_age_seconds is None:
        max_age_seconds = FRESHNESS_SECONDS
    return (time.time() - item["timestamp"]) < max_age_seconds
