import unittest
from admin_panel import AdminPanel
from user_tracker import UserTracker
from rag_system import RAGSystem
import tempfile
import os

class TestAdminPanel(unittest.TestCase):
    def setUp(self):
        # Use temporary DBs for testing
        self.user_db = tempfile.NamedTemporaryFile(delete=False)
        self.rag_db = tempfile.NamedTemporaryFile(delete=False)
        self.user_tracker = UserTracker(self.user_db.name)
        self.rag_system = RAGSystem(self.rag_db.name)
        self.admin_panel = AdminPanel(self.user_tracker, self.rag_system)
        self.client = self.admin_panel.app.test_client()
    
    def tearDown(self):
        # Clean up temp files
        os.unlink(self.user_db.name)
        os.unlink(self.rag_db.name)
    
    def test_login_valid(self):
        # Test valid login
        response = self.client.post('/', data={'password': 'admin123'})
        self.assertEqual(response.status_code, 302)  # Redirect to dashboard
    
    def test_login_invalid(self):
        # Test invalid login
        response = self.client.post('/', data={'password': 'wrong'})
        self.assertIn(b'Invalid Password', response.data)
    
    def test_dashboard_access(self):
        # Test dashboard access after login (simulate session or direct access)
        with self.client:
            response = self.client.get('/dashboard')
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'Admin Dashboard', response.data)
    
    def test_update_pricing(self):
        # Test pricing update
        response = self.client.post('/update_pricing', data={'free_queries': '10', 'premium_price': '150.0'})
        self.assertEqual(response.status_code, 302)  # Redirect
        # Check if config updated (in real test, verify Config values)

if __name__ == '__main__':
    unittest.main()
