# database.py - SQLite persistence layer

import sqlite3
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.connection = None
        self.connect()
        self.init_tables()
    
    def connect(self):
        try:
            self.connection = sqlite3.connect(self.db_path, timeout=10)
            self.connection.row_factory = sqlite3.Row
            logger.info("Database connected")
        except sqlite3.Error as e:
            logger.error(f"DB connection failed: {e}")
            raise
    
    def init_tables(self):
        cursor = self.connection.cursor()
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                user_id INTEGER PRIMARY KEY,
                date TEXT NOT NULL,
                calls INTEGER DEFAULT 0,
                tier TEXT DEFAULT 'free'
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                timestamp INTEGER NOT NULL,
                prompt TEXT NOT NULL,
                response TEXT NOT NULL,
                type TEXT NOT NULL
            )
        """)
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp INTEGER NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL
            )
        """)
        
        self.connection.commit()
    
    def get_usage(self, user_id: int) -> Optional[Dict]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT * FROM usage WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def update_usage(self, user_id: int, date_str: str, calls: int, tier: str):
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO usage (user_id, date, calls, tier)
            VALUES (?, ?, ?, ?)
        """, (user_id, date_str, calls, tier))
        self.connection.commit()
    
    def add_history(self, user_id: int, timestamp: int, prompt: str, response: str, item_type: str):
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO history (user_id, timestamp, prompt, response, type)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, timestamp, prompt, response, item_type))
        self.connection.commit()
        
        # Keep last 50 per user
        cursor.execute("""
            DELETE FROM history WHERE user_id = ? AND id NOT IN (
                SELECT id FROM history WHERE user_id = ? 
                ORDER BY timestamp DESC LIMIT 50
            )
        """, (user_id, user_id))
        self.connection.commit()
    
    def get_recent_history(self, user_id: int, limit: int = 10) -> List[Dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM history WHERE user_id = ? 
            ORDER BY timestamp DESC LIMIT ?
        """, (user_id, limit))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_history_item(self, user_id: int, item_id: int) -> Optional[Dict]:
        cursor = self.connection.cursor()
        cursor.execute("""
            SELECT * FROM history WHERE user_id = ? AND id = ?
        """, (user_id, item_id))
        row = cursor.fetchone()
        return dict(row) if row else None
    
    def log_metric(self, metric_name: str, metric_value: float):
        cursor = self.connection.cursor()
        cursor.execute("""
            INSERT INTO metrics (timestamp, metric_name, metric_value)
            VALUES (?, ?, ?)
        """, (int(time.time()), metric_name, metric_value))
        self.connection.commit()
    
    def close(self):
        if self.connection:
            self.connection.close()
            logger.info("Database closed")