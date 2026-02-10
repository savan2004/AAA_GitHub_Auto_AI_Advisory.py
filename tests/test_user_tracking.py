import unittest
import os
from user_tracker import UserTracker
from config import Config

class TestUserTracking(unittest.TestCase):
    def setUp(self):
        # Use a test DB to avoid affecting real data
        self.db_path = 'test_user_tracking.db'
        self.tracker = UserTracker(self.db_path)
    
    def tearDown(self):
        # Clean up test DB
        if os.path.exists(self.db_path):
            os.remove(self.db_path)
    
    def test_update_user_new(self):
        # Test creating a new user
        self.tracker.update_user(123456, "testuser")
        user = self.tracker.get_user(123456)
        self.assertEqual(user['user_id'], 123456)
        self.assertEqual(user['username'], "testuser")
        self.assertEqual(user['is_premium'], 0)
    
    def test_can_query_free(self):
        # Test free user can query within limit
        self.tracker.update_user(123456)
        self.assertTrue(self.tracker.can_query(123456))
        # Simulate queries
        for _ in range(Config.FREE_QUERIES_PER_DAY):
            self.tracker.log_query(123456, "test", "response")
        self.assertFalse(self.tracker.can_query(123456))  # Limit reached
    
    def test_can_query_premium(self):
        # Test premium user has no limit
        self.tracker.update_user(123456)
        # Manually set premium (in real app, via admin)
        with self.tracker.conn as conn:  # Assuming conn is accessible; adjust if needed
            conn.execute('UPDATE users SET is_premium = 1 WHERE user_id = ?', (123456,))
        self.assertTrue(self.tracker.can_query(123456))
    
    def test_log_query(self):
        # Test logging a query
        self.tracker.update_user(123456)
        self.tracker.log_query(123456, "test query", "test response")
        queries = self.tracker.get_all_queries()
        self.assertEqual(len(queries), 1)
        self.assertEqual(queries[0]['query'], "test query")

if __name__ == '__main__':
    unittest.main()
