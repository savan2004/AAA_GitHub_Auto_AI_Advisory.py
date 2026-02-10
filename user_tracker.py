import sqlite3
from datetime import datetime
from typing import Dict, List

class UserTracker:
    """Safe user tracking with parameterized queries and connection handling."""
    
    def __init__(self, db_path='user_tracking.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    is_premium INTEGER DEFAULT 0,
                    queries_today INTEGER DEFAULT 0,
                    total_queries INTEGER DEFAULT 0,
                    last_query_date TEXT
                )
            ''')
            conn.execute('''
                CREATE TABLE IF NOT EXISTS queries (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER,
                    query TEXT,
                    response TEXT,
                    timestamp TEXT,
                    FOREIGN KEY (user_id) REFERENCES users (user_id)
                )
            ''')
    
    def get_user(self, user_id: int) -> Dict:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
        return {
            'user_id': row[0], 'username': row[1], 'is_premium': row[2],
            'queries_today': row[3], 'total_queries': row[4], 'last_query_date': row[5]
        } if row else {}
    
    def update_user(self, user_id: int, username: str = None):
        today = datetime.now().strftime('%Y-%m-%d')
        with sqlite3.connect(self.db_path) as conn:
            user = self.get_user(user_id)
            if user and user['last_query_date'] != today:
                conn.execute('UPDATE users SET queries_today = 0, last_query_date = ? WHERE user_id = ?', (today, user_id))
            elif not user:
                conn.execute('INSERT INTO users (user_id, username, last_query_date) VALUES (?, ?, ?)', (user_id, username, today))
            conn.commit()
    
    def log_query(self, user_id: int, query: str, response: str):
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT INTO queries (user_id, query, response, timestamp) VALUES (?, ?, ?, ?)', (user_id, query, response, timestamp))
            conn.execute('UPDATE users SET queries_today = queries_today + 1, total_queries = total_queries + 1 WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def is_premium(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return user.get('is_premium', 0) == 1
    
    def can_query(self, user_id: int) -> bool:
        user = self.get_user(user_id)
        return self.is_premium(user_id) or user.get('queries_today', 0) < Config.FREE_QUERIES_PER_DAY
    
    def get_all_users(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM users')
            rows = cursor.fetchall()
        return [{'user_id': row[0], 'username': row[1], 'is_premium': row[2], 'queries_today': row[3], 'total_queries': row[4], 'last_query_date': row[5]} for row in rows]
    
    def get_all_queries(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM queries')
            rows = cursor.fetchall()
        return [{'id': row[0], 'user_id': row[1], 'query': row[2], 'response': row[3], 'timestamp': row[4]} for row in rows]