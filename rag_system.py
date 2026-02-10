import sqlite3
from datetime import datetime
from typing import Dict, List

class RAGSystem:
    """RAG with safe DB handling."""
    
    def __init__(self, db_path='asi_rag.db'):
        self.db_path = db_path
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS historical_data (
                    id INTEGER PRIMARY KEY,
                    symbol TEXT,
                    date TEXT,
                    ltp REAL,
                    rsi REAL,
                    trend TEXT,
                    news TEXT,
                    analysis TEXT
                )
            ''')
    
    def store_data(self, symbol: str, data: Dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT INTO historical_data (symbol, date, ltp, rsi, trend, news, analysis) VALUES (?, ?, ?, ?, ?, ?, ?)',
                         (symbol, datetime.now().strftime('%Y-%m-%d'), data.get('ltp'), data.get('rsi'), data.get('trend'), data.get('news'), data.get('analysis')))
            conn.commit()
    
    def retrieve_context(self, symbol: str, limit=5) -> str:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT date, ltp, rsi, trend, news, analysis FROM historical_data WHERE symbol = ? ORDER BY date DESC LIMIT ?', (symbol, limit))
            rows = cursor.fetchall()
        context = f"Historical data for {symbol}:\n"
        for row in rows:
            context += f"Date: {row[0]}, LTP: {row[1]}, RSI: {row[2]}, Trend: {row[3]}, News: {row[4]}, Analysis: {row[5]}\n"
        return context
    
    def get_all_data(self) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM historical_data')
            rows = cursor.fetchall()
        return [{'id': row[0], 'symbol': row[1], 'date': row[2], 'ltp': row[3], 'rsi': row[4], 'trend': row[5], 'news': row[6], 'analysis': row[7]} for row in rows]