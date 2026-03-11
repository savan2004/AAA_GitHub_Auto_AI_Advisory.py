import time
from collections import defaultdict
from typing import Dict, List, Optional

history_store: Dict[int, List[Dict]] = defaultdict(list)
FRESHNESS_SECONDS = 3600

def add_history_item(uid: int, prompt: str, response: str, itype: str = "analysis") -> int:
    iid = int(time.time())
    history_store[uid].append({
        "id": iid,
        "timestamp": iid,
        "prompt": prompt,
        "response": response,
        "type": itype,
    })
    if len(history_store[uid]) > 20:
        history_store[uid] = history_store[uid][-20:]
    return iid

def get_recent_history(uid: int, limit: int = 10) -> List[Dict]:
    return history_store.get(uid, [])[-limit:][::-1]

def get_history_item(uid: int, iid: int) -> Optional[Dict]:
    for item in history_store.get(uid, []):
        if item["id"] == iid:
            return item
    return None

def is_history_fresh(item: Dict) -> bool:
    return (time.time() - item["timestamp"]) < FRESHNESS_SECONDS
